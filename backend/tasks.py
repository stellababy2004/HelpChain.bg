"""
Automated tasks for HelpChain platform
"""

import json
import logging
import time
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_

try:
    from celery_app import celery
except ImportError:
    from backend.celery_app import celery

from backend.extensions import db
from backend.core.tenant import current_structure_id

try:
    from backend.models import (
        AdminUser,
        ChatMessage,
        HelpRequest,
        Volunteer,
    )
except ImportError:
    from backend.models import (
        AdminUser,
        ChatMessage,
        HelpRequest,
        Volunteer,
    )

try:
    from backend.models_with_analytics import AnalyticsEvent, Task, TaskPerformance
except ImportError:
    from backend.models_with_analytics import AnalyticsEvent, Task, TaskPerformance

try:
    from sentiment_analysis import sentiment_analysis_service
except ImportError:
    try:
        from backend.sentiment_analysis import sentiment_analysis_service
    except ImportError:
        sentiment_analysis_service = None

try:
    from mail_service import send_notification_email
except ImportError:
    from backend.mail_service import send_notification_email

from backend.ai_service import ai_service

try:
    from analytics_service import analytics_service
except ImportError:
    from backend.analytics_service import analytics_service

try:
    from analytics_sample_data import generate_sample_analytics_events
except ImportError:
    from backend.analytics_sample_data import generate_sample_analytics_events

try:
    from smart_matching import smart_matching_service
except ImportError:
    from backend.smart_matching import smart_matching_service


# Import app and mail lazily to avoid circular imports
def _get_app():
    """Get Flask app instance lazily"""
    try:
        from appy import app

        return app
    except ImportError:
        # Fallback for different import paths
        import sys

        if "appy" in sys.modules:
            return sys.modules["appy"].app
        raise


def _get_mail():
    """Get Flask-Mail instance lazily"""
    try:
        from appy import mail

        return mail
    except ImportError:
        # Fallback for different import paths
        import sys

        if "appy" in sys.modules:
            return sys.modules["appy"].mail
        raise


# Try to import Message
try:
    from flask_mail import Message
except ImportError:
    Message = None

try:
    import os

    from redis import Redis
except ImportError:
    import os

logger = logging.getLogger(__name__)


def utc_now() -> datetime:
    """Return naive UTC timestamp without relying on datetime.utcnow."""
    return datetime.now(UTC).replace(tzinfo=None)


def _resolve_structure_id(structure_id: int | None = None) -> int:
    # Background entry points should prefer an explicit structure_id from the
    # caller. Falling back to current_structure_id() is legacy compatibility
    # for in-process/task invocations that still inherit Flask context today.
    # Future out-of-band producers should pass structure_id explicitly and fail
    # closed when tenant resolution is unavailable.
    if structure_id:
        return int(structure_id)
    return int(current_structure_id())


# Email retry configuration
MAX_RETRIES = int(os.getenv("EMAIL_MAX_RETRIES", "6"))
BASE = int(os.getenv("EMAIL_RETRY_BASE_SECONDS", "10"))  # 10s, 20s, 40s, ...


def _save_to_dlq(payload: dict, reason: str):
    """Запиши в DLQ (Redis списък) за по-късно обработване."""
    r = Redis.from_url(os.getenv("CELERY_BROKER_URL", "redis://localhost:6379/0"))
    payload["failed_at"] = utc_now().isoformat()
    payload["reason"] = reason
    r.lpush("dlq:emails", json.dumps(payload))


@celery.task(bind=True)
def auto_match_requests(self):
    """Automatically match pending requests to available volunteers"""
    try:
        logger.info("Starting automatic request matching...")

        # Get pending requests that haven't been matched recently
        pending_requests = (
            db.session.query(HelpRequest)
            .filter(
                and_(
                    HelpRequest.status == "pending",
                    HelpRequest.created_at < utc_now() - timedelta(minutes=30),
                )
            )
            .all()
        )

        matched_count = 0
        for request in pending_requests:
            try:
                # Use smart matching engine to find best volunteer
                matches = smart_matching_service.find_best_matches(request.id, limit=3)

                if matches:
                    best_match = matches[0]
                    volunteer = db.session.get(Volunteer, best_match["volunteer_id"])

                    if volunteer:
                        # Create task automatically
                        task = Task(
                            title=f"Помощ за {request.name}",
                            description=request.problem,
                            location_text=request.location,
                            priority="medium",
                            assigned_to=volunteer.id,
                            help_request_id=request.id,
                            status="assigned",
                            created_by=1,  # System user
                        )
                        db.session.add(task)

                        # Update request status
                        request.status = "assigned"
                        request.assigned_volunteer_id = volunteer.id

                        # Send notification to volunteer
                        self.send_notification.delay(
                            volunteer.email,
                            "Нова автоматично присвоена задача",
                            f"Получихте нова задача: {task.title}",
                        )

                        matched_count += 1
                        logger.info(
                            f"Auto-matched request {request.id} to volunteer {volunteer.name}"
                        )

            except Exception as e:
                logger.error(f"Error matching request {request.id}: {e}")
                continue

        db.session.commit()
        logger.info(f"Auto-matching completed. Matched {matched_count} requests.")

        return {"matched": matched_count, "total": len(pending_requests)}

    except Exception as e:
        logger.error(f"Error in auto_match_requests: {e}")
        db.session.rollback()
        raise self.retry(countdown=60, max_retries=3) from e


@celery.task(bind=True)
def send_reminders(self):
    """Send reminders for overdue tasks and pending requests"""
    try:
        logger.info("Sending automated reminders...")

        # Remind volunteers about overdue tasks
        overdue_tasks = (
            db.session.query(Task)
            .filter(
                and_(
                    Task.status.in_(["assigned", "in_progress"]),
                    Task.created_at < utc_now() - timedelta(days=2),
                )
            )
            .all()
        )

        for task in overdue_tasks:
            volunteer = db.session.get(Volunteer, task.assigned_to)
            if volunteer:
                self.send_notification.delay(
                    volunteer.email,
                    "Напомняне за задача",
                    f'Задачата "{task.title}" очаква изпълнение повече от 2 дни.',
                )

        # Remind admins about pending requests
        pending_requests = (
            db.session.query(HelpRequest)
            .filter(
                and_(
                    HelpRequest.status == "pending",
                    HelpRequest.created_at < utc_now() - timedelta(hours=24),
                )
            )
            .count()
        )

        if pending_requests > 0:
            admins = db.session.query(AdminUser).all()
            for admin in admins:
                self.send_notification.delay(
                    admin.email,
                    "Чакащи заявки",
                    f"Има {pending_requests} чакащи заявки за повече от 24 часа.",
                )

        logger.info(
            f"Reminders sent for {len(overdue_tasks)} tasks and {pending_requests} pending requests"
        )

    except Exception as e:
        logger.error(f"Error in send_reminders: {e}")
        raise self.retry(countdown=300, max_retries=3) from e


@celery.task(bind=True)
def auto_evaluate_tasks(self):
    """Automatically evaluate completed tasks using AI"""
    try:
        logger.info("Starting automatic task evaluation...")

        # Get recently completed tasks without performance records
        completed_tasks = (
            db.session.query(Task)
            .filter(
                and_(
                    Task.status == "completed",
                    Task.completed_at.isnot(None),
                    Task.completed_at > utc_now() - timedelta(hours=24),
                )
            )
            .all()
        )

        evaluated_count = 0
        for task in completed_tasks:
            # Check if performance record already exists
            existing_perf = (
                db.session.query(TaskPerformance).filter_by(task_id=task.id).first()
            )
            if existing_perf:
                continue

            try:
                # Get task details and chat history for AI evaluation
                chat_messages = (
                    db.session.query(ChatMessage)
                    .filter_by(task_id=task.id)
                    .order_by(ChatMessage.timestamp)
                    .all()
                )

                conversation_text = "\n".join(
                    [f"{msg.sender_type}: {msg.message}" for msg in chat_messages]
                )

                # Use AI to evaluate the task
                evaluation_prompt = f"""
                Оцени качеството на изпълнената задача въз основа на разговора:

                Задача: {task.title}
                Описание: {task.description}

                Разговор:
                {conversation_text}

                Оцени по скала 1-5:
                - Качество на комуникацията
                - Ефективност на решението
                - Скорост на отговор
                - Обща удовлетвореност

                Върни JSON с оценки и коментар.
                """

                # TODO: Parse AI response and create performance record
                ai_service.generate_response(evaluation_prompt, "system")

                performance = TaskPerformance(
                    task_id=task.id,
                    volunteer_id=task.assigned_to,
                    quality_rating=4.0,  # Default rating
                    communication_rating=4.0,
                    timeliness_rating=4.0,
                    overall_rating=4.0,
                    ai_feedback="Автоматично оценена задача",
                    evaluated_by=1,  # System user
                    evaluated_at=utc_now(),
                )
                db.session.add(performance)
                evaluated_count += 1

            except Exception as e:
                logger.error(f"Error evaluating task {task.id}: {e}")
                continue

        db.session.commit()
        logger.info(f"Auto-evaluation completed for {evaluated_count} tasks")

    except Exception as e:
        logger.error(f"Error in auto_evaluate_tasks: {e}")
        db.session.rollback()
        raise self.retry(countdown=3600, max_retries=3) from e


@celery.task(bind=True)
def generate_daily_reports(self):
    """Generate daily analytics reports"""
    try:
        logger.info("Generating daily reports...")

        yesterday = utc_now() - timedelta(days=1)
        today_start = yesterday.replace(hour=0, minute=0, second=0, microsecond=0)
        today_end = today_start + timedelta(days=1)

        # Get daily statistics
        new_requests = (
            db.session.query(HelpRequest)
            .filter(HelpRequest.created_at.between(today_start, today_end))
            .count()
        )

        completed_tasks = (
            db.session.query(Task)
            .filter(
                and_(
                    Task.status == "completed",
                    Task.completed_at.between(today_start, today_end),
                )
            )
            .count()
        )

        new_volunteers = (
            db.session.query(Volunteer)
            .filter(Volunteer.created_at.between(today_start, today_end))
            .count()
        )

        # Generate report
        report = f"""
        Дневен отчет HelpChain - {today_start.strftime("%d.%m.%Y")}

        Нови заявки: {new_requests}
        Завършени задачи: {completed_tasks}
        Нови доброволци: {new_volunteers}

        Активни доброволци: {db.session.query(Volunteer).count()}
        Чакащи заявки: {db.session.query(HelpRequest).filter_by(status="pending").count()}
        """

        # Send report to admins
        admins = db.session.query(AdminUser).all()
        for admin in admins:
            self.send_notification.delay(admin.email, "Дневен отчет HelpChain", report)

        # Store in analytics
        analytics_service.track_event(
            event_type="report_generated",
            event_category="automation",
            event_action="daily_report",
            context={
                "new_requests": new_requests,
                "completed_tasks": completed_tasks,
                "new_volunteers": new_volunteers,
                "date": today_start.strftime("%Y-%m-%d"),
            },
        )

        logger.info("Daily report generated and sent")

    except Exception as e:
        logger.error(f"Error in generate_daily_reports: {e}")
        raise self.retry(countdown=3600, max_retries=3) from e


@celery.task(bind=True)
def generate_sample_analytics(self, days=5, events_per_day=48, force=False):
    """Populate analytics tables with deterministic sample data when needed."""

    try:
        app = _get_app()
        with app.app_context():
            created = generate_sample_analytics_events(
                db.session,
                days=days,
                events_per_day=events_per_day,
                force=force,
            )

            if created:
                logger.info("Generated %s sample analytics events", created)
            else:
                logger.debug(
                    "Sample analytics data already present; skipping generation"
                )

            return {"created": created}

    except Exception as exc:
        logger.error(f"Error generating sample analytics data: {exc}")
        db.session.rollback()
        raise self.retry(countdown=300, max_retries=3) from exc


@celery.task(bind=True)
def cleanup_old_data(self):
    """Clean up old logs and temporary data"""
    try:
        logger.info("Starting data cleanup...")

        # Delete old analytics events (older than 90 days)
        cutoff_date = utc_now() - timedelta(days=90)
        deleted_events = (
            db.session.query(AnalyticsEvent)
            .filter(AnalyticsEvent.timestamp < cutoff_date)
            .delete()
        )

        # Delete old chat messages (older than 180 days)
        cutoff_date = utc_now() - timedelta(days=180)
        deleted_messages = (
            db.session.query(ChatMessage)
            .filter(ChatMessage.timestamp < cutoff_date)
            .delete()
        )

        db.session.commit()

        logger.info(
            f"Data cleanup completed. Deleted {deleted_events} events and {deleted_messages} messages"
        )

    except Exception as e:
        logger.error(f"Error in cleanup_old_data: {e}")
        db.session.rollback()
        raise self.retry(countdown=86400, max_retries=3) from e


@celery.task(bind=True)
def send_notification(self, email, subject, message):
    """Send email notification"""
    try:
        send_notification_email(
            recipient=email,
            subject=subject,
            template="email_template.html",
            context={"message": message},
        )
        logger.info(f"Notification sent to {email}")
    except Exception as e:
        logger.error(f"Error sending notification to {email}: {e}")
        raise self.retry(countdown=60, max_retries=3) from e


@celery.task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=600,
    retry_jitter=True,
    max_retries=MAX_RETRIES,
    rate_limit="30/m",
)
def send_email_task(
    self,
    subject: str,
    recipients: list = None,
    body: str = None,
    sender: str = None,
    html: str = None,
    message_id: str = None,
    # Backwards-compatible aliases/kwargs used across the codebase
    recipient: str = None,
    template: str = None,
    context: dict | None = None,
    structure_id: int | None = None,
):
    """
    Надеждно изпращане с автоматичен retry.
    - autoretry_for=Exception: автоматичен retry на всяка грешка
    - retry_backoff: експоненциален backoff (Celery native)
    - retry_jitter: добавя случаен шум, за да няма „шип"
    - max_retries: ограничение
    - rate_limit="30/m": лимит от 30 имейла в минута (за Zoho compliance)
    """
    # Normalize recipients: accept either `recipients` (list) or legacy
    # single `recipient` string used in some call sites.
    if recipients is None and recipient:
        recipients = [recipient]
    recipients = recipients or []

    # If caller supplied a template + context, allow simple extraction of
    # a body string from context['body'] for compatibility with older
    # call sites that pass template/context instead of body/html.
    if not body and context and isinstance(context, dict):
        body = context.get("body") or body

    payload = {
        "subject": subject,
        "recipients": recipients,
        "body": body,
        "sender": sender,
        "html": html,
        "template": template,
        "context": context,
        "message_id": message_id or f"mail-{int(time.time() * 1000)}",
        "structure_id": structure_id,
    }

    try:
        app = _get_app()
        mail = _get_mail()
        with app.app_context():
            sid = _resolve_structure_id(structure_id)
            msg = Message(
                subject=subject,
                sender=sender or app.config["MAIL_DEFAULT_SENDER"],
                recipients=recipients,
            )
            if html:
                msg.html = html
            else:
                msg.body = body

            # идемпотентност: ако имаш БД таблица за изпратени, провери тук и върни early
            # if already_sent(message_id): return "duplicate-skip"

            mail.send(msg)
            app.logger.info(
                "✅ Email sent | to=%s | subject=%s | id=%s | sid=%s",
                recipients,
                subject,
                payload["message_id"],
                sid,
            )
            return "sent"

    except Exception as e:
        # Ако ще има още опити — Celery ще ги планира автоматично (autoretry_for)
        app = _get_app()
        if self.request.retries + 1 >= MAX_RETRIES:
            # Последен fail → DLQ
            _save_to_dlq(payload, reason=str(e))
            app.logger.error(
                "❌ Email permanently failed → DLQ | to=%s | err=%s | id=%s",
                recipients,
                e,
                payload["message_id"],
            )
            return "dlq"
        app.logger.warning(
            "⚠️ Email send failed (retry %s/%s) | to=%s | err=%s",
            self.request.retries + 1,
            MAX_RETRIES,
            recipients,
            e,
        )
        raise  # тригърва Celery autoretry


@celery.task(bind=True)
def retry_failed_emails(self):
    """
    Periodic task to retry emails from the dead letter queue.
    Runs every 30 minutes to attempt resending failed emails.
    """
    import os

    try:
        try:
            from backend.models import FailedEmail
        except Exception:
            try:
                from helpchain_backend.src.models import FailedEmail
            except Exception:
                FailedEmail = None

        logger.info("Starting periodic retry of failed emails from DLQ")

        # Get failed emails older than retry interval (configurable)
        retry_interval_hours = int(os.getenv("EMAIL_RETRY_INTERVAL_HOURS", "24"))
        cutoff_time = utc_now() - timedelta(hours=retry_interval_hours)

        if FailedEmail is None:
            logger.warning("FailedEmail model not available; skipping retry job")
            return {"retried": 0, "total_failed": 0}

        failed_emails = (
            db.session.query(FailedEmail)
            .filter(FailedEmail.created_at < cutoff_time)
            .limit(50)
            .all()
        )  # Process in batches

        retried_count = 0
        for failed_email in failed_emails:
            try:
                # Parse context back to dict
                context = (
                    json.loads(failed_email.context) if failed_email.context else {}
                )

                # Retry sending the email (schedule async task)
                try:
                    self.send_email_with_retry.delay(
                        recipient=failed_email.recipient,
                        subject=failed_email.subject,
                        template=failed_email.template,
                        context=context,
                    )
                except Exception:
                    # If scheduling via Celery fails, log and continue
                    logger.exception("Failed to schedule retry task for failed email")

                # Delete from DLQ after scheduling retry
                try:
                    db.session.delete(failed_email)
                    retried_count += 1
                except Exception:
                    logger.exception("Failed to remove retried FailedEmail from DB")
                    continue

            except Exception as e:
                logger.error(
                    f"Error retrying failed email to {getattr(failed_email, 'recipient', None)}: {e}"
                )
                # Keep in DLQ for next retry cycle
                continue

        try:
            db.session.commit()
        except Exception:
            db.session.rollback()

        logger.info(
            f"Periodic retry completed. Retried {retried_count} emails from DLQ"
        )

        return {"retried": retried_count, "total_failed": len(failed_emails)}

    except Exception as e:
        logger.error(f"Error in retry_failed_emails task: {e}")
        try:
            db.session.rollback()
        except Exception:
            pass
        raise self.retry(countdown=1800, max_retries=3) from e


@celery.task(bind=True)
def requeue_dlq_emails(self, limit: int = 50):
    """
    Взима до N паднали имейли от Redis DLQ и ги пуска отново.
    Периодична задача за повторно опитване на неуспешни имейли.
    """
    try:
        logger.info(f"Starting DLQ requeue process, limit: {limit}")
        r = Redis.from_url(os.getenv("CELERY_BROKER_URL", "redis://localhost:6379/0"))
        requeued_count = 0

        for _ in range(limit):
            raw = r.rpop("dlq:emails")
            if not raw:
                logger.info(f"DLQ is empty after processing {requeued_count} emails")
                break

            try:
                payload = json.loads(raw)

                # Requeue the email with the original parameters
                send_email_task.delay(
                    subject=payload["subject"],
                    recipients=payload["recipients"],
                    body=payload.get("body"),
                    sender=payload.get("sender"),
                    html=payload.get("html"),
                    message_id=payload.get("message_id"),
                    structure_id=payload.get("structure_id"),
                )

                requeued_count += 1
                logger.info(
                    f"Requeued email: {payload.get('message_id')} to {payload['recipients']}"
                )

            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse DLQ payload: {raw}, error: {e}")
                continue
            except KeyError as e:
                logger.error(
                    f"Missing required field in DLQ payload: {e}, payload: {payload}"
                )
                continue
            except Exception as e:
                logger.error(f"Error requeuing email from DLQ: {e}, payload: {payload}")
                # Put it back in DLQ for next attempt
                r.lpush("dlq:emails", raw)
                continue

        # Track analytics for DLQ requeue operation
        if analytics_service:
            analytics_service.track_event(
                event_type="dlq_requeue",
                event_category="email_system",
                event_action="requeue_attempted",
                context={
                    "requeued_count": requeued_count,
                    "limit": limit,
                },
            )

        logger.info(f"DLQ requeue completed. Requeued {requeued_count} emails")
        return {"requeued": requeued_count, "limit": limit}

    except Exception as e:
        logger.error(f"Error in requeue_dlq_emails task: {e}")
        raise self.retry(countdown=300, max_retries=3) from e


@celery.task(bind=True)
def process_ml_analysis(self, request_id):
    """Process ML analysis for help request categorization and volunteer matching"""
    try:
        logger.info(f"Processing ML analysis for request {request_id}")

        # Get request details
        request = db.session.get(HelpRequest, request_id)
        if not request:
            logger.error(f"Request {request_id} not found")
            return

        # AI-powered categorization
        # TODO: Parse AI response and update request
        # ai_service.generate_response(categorization_prompt, "system")

        # Update request with AI categorization
        request.category = "AI_analyzed"  # Placeholder
        request.priority = "medium"  # Placeholder
        request.ai_processed = True

        # Store analysis results in analytics
        analytics_service.track_event(
            event_type="ml_analysis",
            event_category="ai",
            event_action="request_categorized",
            context={
                "request_id": request_id,
                "category": request.category,
                "priority": request.priority,
            },
        )

        db.session.commit()
        logger.info(f"ML analysis completed for request {request_id}")

    except Exception as e:
        logger.error(f"Error in ML analysis for request {request_id}: {e}")
        db.session.rollback()
        raise self.retry(countdown=300, max_retries=3) from e


@celery.task(bind=True)
def update_realtime_stats(self):
    """Update real-time statistics and cache them in Redis"""
    try:
        logger.info("Updating real-time statistics...")

        # Calculate current statistics
        stats = {
            "total_requests": db.session.query(HelpRequest).count(),
            "pending_requests": db.session.query(HelpRequest)
            .filter_by(status="pending")
            .count(),
            "completed_requests": db.session.query(HelpRequest)
            .filter_by(status="completed")
            .count(),
            "total_volunteers": db.session.query(Volunteer).count(),
            "active_volunteers": db.session.query(Volunteer)
            .filter_by(is_active=True)
            .count(),
            "timestamp": utc_now().isoformat(),
        }

        # Cache in Redis (if available)
        try:
            from redis import Redis

            redis_client = Redis.from_url(celery.conf.broker_url)
            redis_client.setex(
                "helpchain:stats", 300, json.dumps(stats)
            )  # Cache for 5 minutes
        except Exception as redis_error:
            logger.warning(f"Redis caching failed: {redis_error}")

        # Store in analytics for historical tracking
        analytics_service.track_event(
            event_type="stats_update",
            event_category="system",
            event_action="realtime_stats",
            context=stats,
        )

        logger.info("Real-time statistics updated")
        return stats

    except Exception as e:
        logger.error(f"Error updating real-time stats: {e}")
        raise self.retry(countdown=60, max_retries=3) from e


@celery.task(bind=True)
def send_bulk_notifications(self, notifications):
    """Send bulk email notifications efficiently"""
    try:
        logger.info(f"Sending bulk notifications to {len(notifications)} recipients")

        success_count = 0
        for notification in notifications:
            try:
                send_notification_email(
                    recipient=notification["email"],
                    subject=notification["subject"],
                    template="email_template.html",
                    context={"message": notification["message"]},
                )
                success_count += 1
            except Exception as e:
                logger.error(
                    f"Failed to send notification to {notification['email']}: {e}"
                )
                continue

        logger.info(
            f"Bulk notifications completed: {success_count}/{len(notifications)} sent"
        )

        # Track analytics
        analytics_service.track_event(
            event_type="bulk_notification",
            event_category="communication",
            event_action="bulk_sent",
            context={
                "total": len(notifications),
                "successful": success_count,
                "failed": len(notifications) - success_count,
            },
        )

    except Exception as e:
        logger.error(f"Error in bulk notifications: {e}")
        raise self.retry(countdown=300, max_retries=3) from e


@celery.task(bind=True)
def process_request_immediately(self, request_data, structure_id: int | None = None):
    """Process new help request immediately with AI analysis"""
    try:
        logger.info("Processing new request immediately")
        sid = _resolve_structure_id(structure_id)

        # Create request in database
        request = HelpRequest(
            name=request_data["name"],
            email=request_data["email"],
            message=request_data["message"],
            status="pending",
        )
        if hasattr(HelpRequest, "structure_id"):
            request.structure_id = sid

        if "category" in request_data:
            request.title = request_data["category"]
        if "location" in request_data:
            request.location_text = request_data["location"]

        db.session.add(request)
        db.session.flush()  # Get request ID

        # Trigger immediate ML analysis
        self.process_ml_analysis.delay(request.id)

        # Try immediate matching
        self.auto_match_requests.delay()

        # Send confirmation email
        confirmation_message = f"""
        Здравейте {request.name},

        Вашата заявка за помощ е получена успешно.

        Ще се свържем с вас скоро след като намерим подходящ доброволец.

        Детайли на заявката:
        - Категория: {request.title or "Не е посочена"}
        - Локация: {request.location_text or "Не е посочена"}
        - Съобщение: {request.message}

        Благодаря, че използвате HelpChain!
        """

        self.send_notification.delay(
            request.email,
            "Заявката ви е получена - HelpChain",
            confirmation_message,
        )

        db.session.commit()
        logger.info(f"Request {request.id} processed immediately")

        return {"request_id": request.id, "status": "processed"}

    except Exception as e:
        logger.error(f"Error processing request immediately: {e}")
        db.session.rollback()
        raise self.retry(countdown=60, max_retries=3) from e


@celery.task(bind=True)
def generate_performance_report(self, volunteer_id=None, period="weekly"):
    """Generate detailed performance report for volunteer or all volunteers"""
    try:
        logger.info(f"Generating performance report for period: {period}")

        # Calculate date range
        if period == "weekly":
            start_date = utc_now() - timedelta(days=7)
        elif period == "monthly":
            start_date = utc_now() - timedelta(days=30)
        else:  # daily
            start_date = utc_now() - timedelta(days=1)

        # Get performance data
        if volunteer_id:
            # Single volunteer report
            volunteer = db.session.get(Volunteer, volunteer_id)
            if not volunteer:
                return {"error": "Volunteer not found"}

            performances = (
                db.session.query(TaskPerformance)
                .filter(
                    and_(
                        TaskPerformance.volunteer_id == volunteer_id,
                        TaskPerformance.evaluated_at >= start_date,
                    )
                )
                .all()
            )

            report = {
                "volunteer_name": volunteer.name,
                "volunteer_email": volunteer.email,
                "period": period,
                "total_tasks": len(performances),
                "avg_rating": (
                    sum(p.overall_rating for p in performances) / len(performances)
                    if performances
                    else 0
                ),
                "tasks_completed": len(
                    [p for p in performances if p.task.status == "completed"]
                ),
            }
        else:
            # All volunteers report
            performances = (
                db.session.query(TaskPerformance)
                .filter(TaskPerformance.evaluated_at >= start_date)
                .all()
            )

            volunteer_stats = {}
            for perf in performances:
                vid = perf.volunteer_id
                if vid not in volunteer_stats:
                    volunteer_stats[vid] = {
                        "tasks": 0,
                        "total_rating": 0,
                        "completed": 0,
                    }
                volunteer_stats[vid]["tasks"] += 1
                volunteer_stats[vid]["total_rating"] += perf.overall_rating
                if perf.task.status == "completed":
                    volunteer_stats[vid]["completed"] += 1

            report = {
                "period": period,
                "total_performances": len(performances),
                "volunteers": [
                    {
                        "volunteer_id": vid,
                        "avg_rating": stats["total_rating"] / stats["tasks"],
                        "tasks_completed": stats["completed"],
                        "total_tasks": stats["tasks"],
                    }
                    for vid, stats in volunteer_stats.items()
                ],
            }

        # Send report via email
        if volunteer_id:
            volunteer = db.session.get(Volunteer, volunteer_id)
            self.send_notification.delay(
                volunteer.email,
                f"Отчет за представянето - {period}",
                f"Вашият отчет за периода е готов. Средна оценка: {report['avg_rating']:.1f}",
            )
        else:
            # Send to admins
            admins = db.session.query(AdminUser).all()
            for admin in admins:
                self.send_notification.delay(
                    admin.email,
                    f"Общ отчет за представянето - {period}",
                    f"Отчетът за всички доброволци е готов. Общо оценки: {report['total_performances']}",
                )

        logger.info(f"Performance report generated for period: {period}")
        return report

    except Exception as e:
        logger.error(f"Error generating performance report: {e}")
        raise self.retry(countdown=3600, max_retries=3) from e


@celery.task(bind=True)
def monitor_system_health(self):
    """Monitor system health and send alerts if needed"""
    try:
        logger.info("Monitoring system health...")

        # Check database connectivity (ensure Result is closed promptly)
        _conn_res = db.session.execute("SELECT 1")
        try:
            _ = _conn_res.first()
        finally:
            try:
                _conn_res.close()
            except Exception:
                pass

        # Check pending requests count
        pending_count = (
            db.session.query(HelpRequest).filter_by(status="pending").count()
        )
        if pending_count > 50:  # Alert threshold
            admins = db.session.query(AdminUser).all()
            for admin in admins:
                self.send_notification.delay(
                    admin.email,
                    "Системно предупреждение",
                    f"Висок брой чакащи заявки: {pending_count}",
                )

        # Check volunteer availability
        active_volunteers = (
            db.session.query(Volunteer).filter_by(is_active=True).count()
        )
        if active_volunteers < 5:  # Alert threshold
            admins = db.session.query(AdminUser).all()
            for admin in admins:
                self.send_notification.delay(
                    admin.email,
                    "Системно предупреждение",
                    f"Нисък брой активни доброволци: {active_volunteers}",
                )

        logger.info("System health check completed")

    except Exception as e:
        logger.error(f"Error in monitor_system_health: {e}")
        raise self.retry(countdown=300, max_retries=3) from e


@celery.task(bind=True)
def process_feedback_sentiment(self, feedback_id):
    """Process sentiment analysis for user feedback"""
    try:
        logger.info(f"Processing sentiment analysis for feedback {feedback_id}")

        if sentiment_analysis_service is None:
            logger.warning(
                f"Sentiment analysis service not available, skipping feedback {feedback_id}"
            )
            return {"error": "sentiment_analysis_service_not_available"}

        result = sentiment_analysis_service.analyze_feedback(feedback_id)

        if "error" in result:
            logger.error(f"Error analyzing feedback {feedback_id}: {result['error']}")
            raise self.retry(countdown=60, max_retries=3)
        else:
            sentiment_label = result.get("sentiment_label", "unknown")
            logger.info(
                f"Successfully analyzed sentiment for feedback {feedback_id}: {sentiment_label}"
            )

        return result

    except Exception as e:
        logger.error(
            f"Error in process_feedback_sentiment for feedback {feedback_id}: {e}"
        )
        raise self.retry(countdown=300, max_retries=3) from e
