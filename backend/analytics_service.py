"""
Advanced Analytics Service for HelpChain
Provides comprehensive analytics and user behavior tracking
"""

import datetime
import json
import logging
import os
import random
import re
import statistics
import time
from collections import Counter, defaultdict
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from functools import wraps
from typing import Any, Optional, Union

from flask import current_app
from sqlalchemy import and_, case, func, text
from sqlalchemy.orm import Session

try:
    from backend.models_with_analytics import (
        AnalyticsEvent,
        ChatbotConversation,
        PerformanceMetrics,
        User,
        UserBehavior,
        Volunteer,
        utc_now,
    )
except Exception:
    try:
        from models_with_analytics import (
            AnalyticsEvent,
            ChatbotConversation,
            PerformanceMetrics,
            User,
            UserBehavior,
            Volunteer,
            utc_now,
        )
    except Exception:
        # Serverless / preview safety: always use absolute backend import
        from backend.models import (
            AnalyticsEvent,
            ChatbotConversation,
            PerformanceMetrics,
            User,
            UserBehavior,
            Volunteer,
            utc_now,
        )

logger = logging.getLogger(__name__)

try:
    from .performance_optimization import DatabaseOptimizer
except ImportError:
    try:
        from performance_optimization import DatabaseOptimizer
    except ImportError:
        DatabaseOptimizer = None


def get_db():
    """Get the database instance from current Flask app context"""
    from flask import current_app

    if current_app and "sqlalchemy" in current_app.extensions:
        return current_app.extensions["sqlalchemy"]

    # Fallback - use canonical backend.extensions
    try:
        from backend.extensions import db

        return db
    except ImportError:
        raise RuntimeError("Could not get database instance") from None


class AdvancedAnalytics:
    """Advanced Analytics Service"""

    def __init__(self, db=None):
        self.cache_duration = 300  # 5 минути кеш
        self._cache = {}
        self.db = db  # Database session passed during initialization

    def cached_analytics_method(self, cache_key_prefix: str, duration: int = None):
        """Decorator for caching analytics method results"""

        def decorator(func):
            @wraps(func)
            def wrapper(*args, **kwargs):
                if duration is None:
                    cache_duration = self.cache_duration
                else:
                    cache_duration = duration

                # Generate cache key from method name and arguments
                cache_key = f"{cache_key_prefix}_{hash(str(args) + str(kwargs))}"

                # Check cache
                if self._is_cached(cache_key, cache_duration):
                    return self._cache[cache_key]

                # Execute method
                result = func(*args, **kwargs)

                # Cache result
                self._cache[cache_key] = result
                self._cache[f"{cache_key}_timestamp"] = time.time()

                return result

            return wrapper

        return decorator

    def track_event(
        self,
        event_type: str,
        event_category: str = None,
        event_action: str = None,
        event_label: str = None,
        event_value: int = None,
        context: dict[str, Any] = None,
    ):
        """Проследява събитие за аналитика с thread safety"""
        return self._safe_database_operation(
            self._track_event_impl,
            event_type,
            event_category,
            event_action,
            event_label,
            event_value,
            context,
        )

    def _track_event_impl(
        self,
        event_type: str,
        event_category: str = None,
        event_action: str = None,
        event_label: str = None,
        event_value: int = None,
        context: dict[str, Any] = None,
    ):
        """Internal implementation of track_event"""
        try:
            # Validate required fields
            if not event_type or not event_type.strip():
                logger.error("Event type is required and cannot be empty")
                return False

            context = context or {}

            event = AnalyticsEvent(
                event_type=event_type,
                event_category=event_category,
                event_action=event_action,
                event_label=event_label,
                event_value=event_value,
                user_session=context.get("session_id"),
                user_type=context.get("user_type", "guest"),
                user_ip=context.get("ip_address"),
                user_agent=context.get("user_agent"),
                page_url=context.get("page_url"),
                page_title=context.get("page_title"),
                referrer=context.get("referrer"),
                load_time=context.get("load_time"),
                screen_resolution=context.get("screen_resolution"),
                device_type=context.get("device_type", "unknown"),
            )

            self.db.add(event)
            self.db.commit()

            # Обновяваме user behavior
            self._update_user_behavior_impl(context)

            return True

        except Exception as e:
            logger.error(f"Error tracking event: {e}")
            try:
                self.db.rollback()
            except:
                pass
            return False

    def track_performance(
        self,
        metric_type: str,
        metric_name: str,
        metric_value: float,
        unit: str = None,
        context: dict[str, Any] = None,
    ):
        """Проследява метрики за производителност с thread safety"""
        return self._safe_database_operation(
            self._track_performance_impl,
            metric_type,
            metric_name,
            metric_value,
            unit,
            context,
        )

    def _safe_database_operation(self, operation, *args, **kwargs):
        """Безопасно извършва database операция с proper Flask app context"""
        try:
            from flask import has_app_context

            # Проверяваме дали имаме Flask app context
            if has_app_context():
                # Ако сме в request context, изпълняваме директно
                return operation(*args, **kwargs)
            else:
                # Ако сме в background thread, трябва да създадем app context
                logger.warning(
                    "No Flask app context - skipping database operation for thread safety"
                )
                return False
        except Exception as e:
            logger.error(f"Error in safe database operation: {e}")
            return False

    def _track_performance_impl(
        self,
        metric_type: str,
        metric_name: str,
        metric_value: float,
        unit: str = None,
        context: dict[str, Any] = None,
    ):
        """Internal implementation of track_performance"""
        try:
            context = context or {}

            metric = PerformanceMetrics(
                metric_type=metric_type,
                metric_name=metric_name,
                metric_value=metric_value,
                unit=unit,
                endpoint=context.get("endpoint"),
                user_agent=context.get("user_agent"),
                request_size=context.get("request_size"),
                response_size=context.get("response_size"),
                context_data=json.dumps(context.get("metadata", {})),
            )

            self.db.add(metric)
            self.db.commit()

            return True

        except Exception as e:
            logger.error(f"Error tracking performance: {e}")
            self.db.rollback()
            return False

    def update_user_behavior(self, context: dict[str, Any]):
        """Обновява потребителското поведение с thread safety"""
        return self._safe_database_operation(
            self._update_user_behavior_impl,
            context,
        )

    def _update_user_behavior_impl(self, context: dict[str, Any]):
        """Обновява потребителското поведение"""
        try:
            session_id = context.get("session_id")
            if not session_id:
                return

            behavior = (
                self.db.query(UserBehavior).filter_by(session_id=session_id).first()
            )

            if not behavior:
                behavior = UserBehavior(
                    session_id=session_id,
                    user_type=context.get("user_type", "guest"),
                    entry_page=context.get("page_url"),
                    ip_address=context.get("ip_address"),
                    user_agent=context.get("user_agent"),
                    device_info=context.get("device_type"),
                    location=context.get("location"),
                )
                self.db.add(behavior)

            # Обновяваме метриките
            behavior.pages_visited = (behavior.pages_visited or 0) + 1
            behavior.last_activity = utc_now()
            behavior.exit_page = context.get("page_url")

            if behavior.pages_visited > 1:
                behavior.bounce_rate = False

            # Обновяваме sequence от посетени страници
            pages_sequence = json.loads(behavior.pages_sequence or "[]")
            pages_sequence.append(
                {
                    "url": context.get("page_url"),
                    "timestamp": utc_now().isoformat(),
                    "title": context.get("page_title"),
                }
            )
            behavior.pages_sequence = json.dumps(
                pages_sequence[-20:]
            )  # Последните 20 страници

            self.db.commit()

        except Exception as e:
            logger.error(f"Error updating user behavior: {e}")
            self.db.rollback()

    def _normalize_period(
        self,
        *,
        days: int = 30,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
    ) -> tuple[datetime, datetime, int]:
        """Normalize period boundaries similar to admin analytics engine."""
        end = end_date or utc_now()
        start = start_date or end - timedelta(days=max(days - 1, 0))

        if start > end:
            start, end = end, start

        if not isinstance(start, datetime):
            start = datetime.combine(start, datetime.min.time())
        else:
            start = start.replace(hour=0, minute=0, second=0, microsecond=0)

        if not isinstance(end, datetime):
            end = datetime.combine(end, datetime.max.time())
        else:
            end = end.replace(hour=23, minute=59, second=59, microsecond=999999)

        period_days = max(1, (end.date() - start.date()).days + 1)
        return start, end, period_days

    def get_dashboard_analytics(
        self,
        days: int = 30,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
    ) -> dict[str, Any]:
        """Получава подробна аналитика за dashboard"""
        try:
            from flask import current_app

            demo_mode = bool(current_app.config.get("DEMO_MODE")) if current_app else False
            if not current_app:
                return _minimal_analytics()

            start_dt, end_dt, period_days = self._normalize_period(
                days=days, start_date=start_date, end_date=end_date
            )

            cache_key = "_".join(
                [
                    "dashboard_analytics",
                    start_dt.strftime("%Y%m%d"),
                    end_dt.strftime("%Y%m%d"),
                ]
            )
            if self._is_cached(cache_key):
                return self._cache[cache_key]

            analytics = {
                "overview": self._get_overview_metrics(start_dt, end_dt),
                "user_engagement": self._get_user_engagement(start_dt, end_dt),
                "chatbot_analytics": self._get_chatbot_analytics(start_dt, end_dt),
                "performance_metrics": self._get_performance_metrics(start_dt, end_dt),
                "conversion_funnel": self._get_conversion_funnel(start_dt, end_dt),
                "user_journey": self._get_user_journey_analytics(start_dt, end_dt),
                "real_time": self._get_real_time_metrics(),
                "period": {
                    "start": start_dt.isoformat(),
                    "end": end_dt.isoformat(),
                    "days": period_days,
                },
            }

            # Empty production datasets must stay empty. Sample analytics are only
            # allowed when DEMO_MODE is explicitly enabled.
            has_data = (
                analytics["overview"]["total_page_views"] > 0
                or analytics["overview"]["unique_visitors"] > 0
                or analytics["chatbot_analytics"]["total_conversations"] > 0
            )
            if not has_data and demo_mode:
                analytics = self._get_sample_analytics()
            elif not has_data:
                analytics["data_availability"] = {
                    "available": False,
                    "reason": "No analytics events, visitor sessions, or chatbot conversations were found for this period.",
                    "confidence": "high",
                    "source_tables": [
                        "analytics_events",
                        "user_behaviors",
                        "chatbot_conversations",
                    ],
                    "last_updated": utc_now().isoformat(),
                }
                analytics["is_sample_data"] = False

            self._cache[cache_key] = analytics
            return analytics

        except Exception as e:
            logger.error(f"Error getting dashboard analytics: {e}")
            try:
                if current_app and current_app.config.get("DEMO_MODE"):
                    return self._get_sample_analytics()
            except Exception:
                pass
            return _minimal_analytics()

    def _get_overview_metrics(
        self, start_date: datetime, end_date: datetime
    ) -> dict[str, Any]:
        """Общи метрики за периода - optimized with indexes and caching"""
        try:
            from flask import current_app

            if not current_app:
                return {
                    "unique_visitors": 0,
                    "total_page_views": 0,
                    "avg_session_time": 0,
                    "bounce_rate": 0,
                    "conversion_rate": 0,
                }

            # Check cache first (longer cache duration for overview metrics)
            cache_key = f"overview_{start_date}_{end_date}"
            if self._is_cached(cache_key, 600):  # 10 minute cache
                return self._cache[cache_key]

            # Optimized queries using new indexes

            # Уникални посетители (по session_id) - uses idx_user_behaviors_session_start
            unique_visitors = (
                self.db.query(func.count(func.distinct(UserBehavior.session_id)))
                .filter(UserBehavior.session_start.between(start_date, end_date))
                .scalar()
                or 0
            )

            # Общо page views - uses idx_analytics_timestamp and idx_analytics_event_type
            total_page_views = (
                self.db.query(func.count(AnalyticsEvent.id))
                .filter(
                    and_(
                        AnalyticsEvent.event_type == "page_view",
                        AnalyticsEvent.created_at.between(start_date, end_date),
                    )
                )
                .scalar()
                or 0
            )

            # Средно време на сесия - uses idx_user_behaviors_session_start
            avg_session_time = (
                self.db.query(func.avg(UserBehavior.total_time_spent))
                .filter(UserBehavior.session_start.between(start_date, end_date))
                .scalar()
                or 0
            )

            # Bounce rate calculation - optimized with single query
            session_stats = (
                self.db.query(
                    func.count(UserBehavior.id).label("total_sessions"),
                    func.sum(
                        case((UserBehavior.bounce_rate.is_(True), 1), else_=0)
                    ).label("bounced_sessions"),
                    func.sum(
                        case(
                            (UserBehavior.conversion_action.isnot(None), 1),
                            else_=0,
                        )
                    ).label("conversions"),
                )
                .filter(UserBehavior.session_start.between(start_date, end_date))
                .first()
            )

            total_sessions = session_stats.total_sessions or 0
            bounced_sessions = session_stats.bounced_sessions or 0
            conversions = session_stats.conversions or 0

            bounce_rate = (
                (bounced_sessions / total_sessions * 100) if total_sessions > 0 else 0
            )
            conversion_rate = (
                (conversions / total_sessions * 100) if total_sessions > 0 else 0
            )

            result = {
                "unique_visitors": unique_visitors,
                "total_page_views": total_page_views,
                "avg_session_time": (
                    round(avg_session_time / 60, 2) if avg_session_time else 0
                ),  # в минути
                "bounce_rate": round(bounce_rate, 2),
                "total_sessions": total_sessions,
                "conversions": conversions,
                "conversion_rate": round(conversion_rate, 2),
            }

            # Cache the result
            self._cache[cache_key] = result
            self._cache[f"{cache_key}_timestamp"] = time.time()
            return result
        except Exception as e:
            logger.error(f"Error in _get_overview_metrics: {e}")
            return {
                "unique_visitors": 0,
                "total_page_views": 0,
                "avg_session_time": 0,
                "bounce_rate": 0,
                "conversion_rate": 0,
            }

    def _get_user_engagement(
        self, start_date: datetime, end_date: datetime
    ) -> dict[str, Any]:
        """Метрики за потребителската ангажираност - optimized with indexes"""
        try:
            from performance_optimization import DatabaseOptimizer

            # Use optimized queries from performance_optimization.py
            optimized_queries = DatabaseOptimizer.get_optimized_analytics_queries()

            # Най-посещавани страници - uses optimized query with index
            _ = optimized_queries.get("top_pages")  # retained for compatibility
            top_pages_sql = text("""
                SELECT page_url AS page,
                       COUNT(*) AS visits
                FROM analytics_events
                WHERE event_type = 'page_view'
                  AND created_at BETWEEN :start_date AND :end_date
                GROUP BY page_url
                ORDER BY visits DESC
                LIMIT 10
                """)
            _top_res = self.db.execute(
                top_pages_sql,
                {"start_date": start_date, "end_date": end_date},
            )
            try:
                top_pages_rows = _top_res.fetchall()
            finally:
                try:
                    _top_res.close()
                except Exception:
                    pass

            blocked_prefixes = (
                "/admin",
                "/ops",
                "/events",
                "/api",
                "/static",
                "/favicon",
            )

            top_pages = [
                {"url": row[0], "views": row[1]}
                for row in top_pages_rows
                if row[0] and not str(row[0]).startswith(blocked_prefixes)
            ]

            # Device breakdown - uses idx_user_behaviors_session_start
            device_stats = (
                self.db.query(
                    UserBehavior.device_info,
                    func.count(UserBehavior.id).label("sessions"),
                )
                .filter(UserBehavior.session_start.between(start_date, end_date))
                .group_by(UserBehavior.device_info)
                .order_by(func.count(UserBehavior.id).desc())
                .limit(10)
                .all()
            )

            # Hourly activity pattern - optimized with single query instead of 24 separate queries
            hourly_activity_result = (
                self.db.query(
                    func.extract("hour", AnalyticsEvent.created_at).label("hour"),
                    func.count(AnalyticsEvent.id).label("activity"),
                )
                .filter(
                    and_(
                        AnalyticsEvent.event_type == "page_view",
                        AnalyticsEvent.created_at.between(start_date, end_date),
                    )
                )
                .group_by(func.extract("hour", AnalyticsEvent.created_at))
                .order_by(func.extract("hour", AnalyticsEvent.created_at))
                .all()
            )

            # Convert to dict for easy lookup, then fill in missing hours with 0
            hourly_dict = {
                int(row.hour): row.activity for row in hourly_activity_result
            }
            hourly_activity = [
                {"hour": hour, "activity": hourly_dict.get(hour, 0)}
                for hour in range(24)
            ]

            return {
                "top_pages": top_pages,
                "device_breakdown": [
                    {"device": device or "Unknown", "sessions": sessions}
                    for device, sessions in device_stats
                ],
                "hourly_activity": hourly_activity,
            }
        except Exception as e:
            logger.error(f"Error in _get_user_engagement: {e}")
            # Fallback to basic queries
            top_pages = (
                self.db.query(
                    AnalyticsEvent.page_url,
                    func.count(AnalyticsEvent.id).label("views"),
                )
                .filter(
                    and_(
                        AnalyticsEvent.event_type == "page_view",
                        AnalyticsEvent.created_at.between(start_date, end_date),
                        AnalyticsEvent.page_url.isnot(None),
                    )
                )
                .group_by(AnalyticsEvent.page_url)
                .order_by(func.count(AnalyticsEvent.id).desc())
                .limit(10)
                .all()
            )

            device_stats = (
                self.db.query(
                    UserBehavior.device_info,
                    func.count(UserBehavior.id).label("sessions"),
                )
                .filter(UserBehavior.session_start.between(start_date, end_date))
                .group_by(UserBehavior.device_info)
                .all()
            )

            return {
                "top_pages": [{"url": url, "views": views} for url, views in top_pages],
                "device_breakdown": [
                    {"device": device or "Unknown", "sessions": sessions}
                    for device, sessions in device_stats
                ],
                "hourly_activity": [{"hour": h, "activity": 0} for h in range(24)],
            }

    def _get_chatbot_analytics(
        self, start_date: datetime, end_date: datetime
    ) -> dict[str, Any]:
        """Аналитика на чатбота"""

        try:
            optimized_queries = DatabaseOptimizer.get_optimized_analytics_queries()

            # Общо разговори - optimized count query
            total_conversations = (
                self.db.query(ChatbotConversation)
                .filter(ChatbotConversation.created_at.between(start_date, end_date))
                .count()
            )

            # По тип отговор - uses optimized query with index
            response_types_query = optimized_queries["chatbot_conversations_summary"]
            _resp_res = self.db.execute(
                text(response_types_query),
                {"start_date": start_date, "end_date": end_date},
            )
            try:
                response_types_rows = _resp_res.fetchall()
            finally:
                try:
                    _resp_res.close()
                except Exception:
                    pass

            response_types = {row[0]: row[1] for row in response_types_rows}

            # AI статистики - uses optimized query with index
            ai_stats_query = optimized_queries["chatbot_ai_stats"]
            _ai_res = self.db.execute(
                text(ai_stats_query), {"start_date": start_date, "end_date": end_date}
            )
            try:
                ai_stats_result = _ai_res.fetchone()
            finally:
                try:
                    _ai_res.close()
                except Exception:
                    pass

            ai_stats = {
                "total_ai_responses": ai_stats_result[0] or 0,
                "avg_confidence": round(ai_stats_result[1] or 0, 3),
                "avg_processing_time": round(ai_stats_result[2] or 0, 3),
                "total_tokens": ai_stats_result[3] or 0,
            }

            # User ratings - uses optimized query with index
            ratings_query = optimized_queries["chatbot_ratings"]
            _ratings_res = self.db.execute(
                text(ratings_query), {"start_date": start_date, "end_date": end_date}
            )
            try:
                ratings_result = _ratings_res.fetchone()
            finally:
                try:
                    _ratings_res.close()
                except Exception:
                    pass

            avg_rating = round(ratings_result[1] or 0, 2)
            rated_conversations = ratings_result[0] or 0

            return {
                "total_conversations": total_conversations,
                "response_types": response_types,
                "ai_statistics": ai_stats,
                "average_rating": avg_rating,
                "rated_conversations": rated_conversations,
            }
        except Exception as e:
            logger.error(f"Error in _get_chatbot_analytics: {e}")
            # Fallback to basic queries
            return self._get_chatbot_analytics_fallback(start_date, end_date)

    def _get_chatbot_analytics_fallback(
        self, start_date: datetime, end_date: datetime
    ) -> dict[str, Any]:
        """Fallback method for chatbot analytics when optimized queries fail"""

        # Общо разговори
        total_conversations = (
            self.db.query(ChatbotConversation)
            .filter(ChatbotConversation.created_at.between(start_date, end_date))
            .count()
        )

        # По тип отговор
        response_types = (
            self.db.query(
                ChatbotConversation.response_type,
                func.count(ChatbotConversation.id).label("count"),
            )
            .filter(ChatbotConversation.created_at.between(start_date, end_date))
            .group_by(ChatbotConversation.response_type)
            .all()
        )

        # AI статистики
        ai_conversations = (
            self.db.query(ChatbotConversation)
            .filter(
                and_(
                    ChatbotConversation.response_type == "ai",
                    ChatbotConversation.created_at.between(start_date, end_date),
                )
            )
            .all()
        )

        ai_stats = {
            "total_ai_responses": len(ai_conversations),
            "avg_confidence": (
                sum(c.ai_confidence or 0 for c in ai_conversations)
                / len(ai_conversations)
                if ai_conversations
                else 0
            ),
            "avg_processing_time": (
                sum(c.processing_time or 0 for c in ai_conversations)
                / len(ai_conversations)
                if ai_conversations
                else 0
            ),
            "total_tokens": sum(c.ai_tokens_used or 0 for c in ai_conversations),
        }

        # User ratings
        rated_conversations = (
            self.db.query(ChatbotConversation)
            .filter(
                and_(
                    ChatbotConversation.user_rating.isnot(None),
                    ChatbotConversation.created_at.between(start_date, end_date),
                )
            )
            .all()
        )

        avg_rating = (
            sum(c.user_rating for c in rated_conversations) / len(rated_conversations)
            if rated_conversations
            else 0
        )

        return {
            "total_conversations": total_conversations,
            "response_types": dict(response_types),
            "ai_statistics": ai_stats,
            "average_rating": round(avg_rating, 2),
            "rated_conversations": len(rated_conversations),
        }

    def _get_performance_metrics(
        self, start_date: datetime, end_date: datetime
    ) -> dict[str, Any]:
        """Метрики за производителност"""

        try:
            optimized_queries = DatabaseOptimizer.get_optimized_analytics_queries()

            # Average response times by endpoint - uses optimized query with index
            performance_query = optimized_queries["performance_by_endpoint"]
            _ep_res = self.db.execute(
                text(performance_query),
                {"start_date": start_date, "end_date": end_date},
            )
            try:
                endpoint_performance_rows = _ep_res.fetchall()
            finally:
                try:
                    _ep_res.close()
                except Exception:
                    pass

            endpoint_performance = [
                {
                    "endpoint": row[0],
                    "avg_time": round(row[1], 3),
                    "request_count": row[2],
                }
                for row in endpoint_performance_rows
            ]

            # System load over time - optimized single query for all days instead of loop
            daily_performance_result = (
                self.db.query(
                    func.date(PerformanceMetrics.created_at).label("date"),
                    func.avg(PerformanceMetrics.metric_value).label(
                        "avg_response_time"
                    ),
                )
                .filter(
                    and_(
                        PerformanceMetrics.metric_type == "response_time",
                        PerformanceMetrics.created_at.between(start_date, end_date),
                    )
                )
                .group_by(func.date(PerformanceMetrics.created_at))
                .order_by(func.date(PerformanceMetrics.created_at))
                .all()
            )

            # Convert to the expected format
            daily_performance = [
                {
                    "date": row.date.strftime("%Y-%m-%d"),
                    "avg_response_time": round(row.avg_response_time or 0, 3),
                }
                for row in daily_performance_result
            ]

            return {
                "endpoint_performance": endpoint_performance,
                "daily_performance": daily_performance,
            }
        except Exception as e:
            logger.error(f"Error in _get_performance_metrics: {e}")
            # Fallback to basic queries
            return self._get_performance_metrics_fallback(start_date, end_date)

    def _get_performance_metrics_fallback(
        self, start_date: datetime, end_date: datetime
    ) -> dict[str, Any]:
        """Fallback method for performance metrics when optimized queries fail"""

        # Average response times by endpoint
        response_times = (
            self.db.query(
                PerformanceMetrics.endpoint,
                func.avg(PerformanceMetrics.metric_value).label("avg_time"),
            )
            .filter(
                and_(
                    PerformanceMetrics.metric_type == "response_time",
                    PerformanceMetrics.created_at.between(start_date, end_date),
                    PerformanceMetrics.endpoint.isnot(None),
                )
            )
            .group_by(PerformanceMetrics.endpoint)
            .order_by(func.avg(PerformanceMetrics.metric_value).desc())
            .limit(10)
            .all()
        )

        # System load over time
        daily_performance = []
        current_date = start_date
        while current_date <= end_date:
            next_date = current_date + timedelta(days=1)

            avg_response = (
                self.db.query(func.avg(PerformanceMetrics.metric_value))
                .filter(
                    and_(
                        PerformanceMetrics.metric_type == "response_time",
                        PerformanceMetrics.created_at.between(current_date, next_date),
                    )
                )
                .scalar()
                or 0
            )

            daily_performance.append(
                {
                    "date": current_date.strftime("%Y-%m-%d"),
                    "avg_response_time": round(avg_response, 3),
                }
            )

            current_date = next_date

        return {
            "endpoint_performance": [
                {"endpoint": ep, "avg_time": round(time, 3)}
                for ep, time in response_times
            ],
            "daily_performance": daily_performance,
        }

    def _get_conversion_funnel(
        self, start_date: datetime, end_date: datetime
    ) -> dict[str, Any]:
        """Анализ на conversion funnel"""

        try:
            optimized_queries = DatabaseOptimizer.get_optimized_analytics_queries()

            # Get all funnel metrics with single optimized queries
            visitors_query = optimized_queries["conversion_funnel_visitors"]
            _tv_res = self.db.execute(
                text(visitors_query),
                {"start_date": start_date, "end_date": end_date},
            )
            try:
                total_visitors = _tv_res.scalar() or 0
            finally:
                try:
                    _tv_res.close()
                except Exception:
                    pass

            register_visits_query = optimized_queries[
                "conversion_funnel_register_visits"
            ]
            _vr_res = self.db.execute(
                text(register_visits_query),
                {"start_date": start_date, "end_date": end_date},
            )
            try:
                visited_register = _vr_res.scalar() or 0
            finally:
                try:
                    _vr_res.close()
                except Exception:
                    pass

            registrations_query = optimized_queries["conversion_funnel_registrations"]
            _sr_res = self.db.execute(
                text(registrations_query),
                {"start_date": start_date, "end_date": end_date},
            )
            try:
                started_registration = _sr_res.scalar() or 0
            finally:
                try:
                    _sr_res.close()
                except Exception:
                    pass

            completions_query = optimized_queries["conversion_funnel_completions"]
            _cr_res = self.db.execute(
                text(completions_query),
                {"start_date": start_date, "end_date": end_date},
            )
            try:
                completed_registration = _cr_res.scalar() or 0
            finally:
                try:
                    _cr_res.close()
                except Exception:
                    pass

            chatbot_users_query = optimized_queries["conversion_funnel_chatbot_users"]
            _cu_res = self.db.execute(
                text(chatbot_users_query),
                {"start_date": start_date, "end_date": end_date},
            )
            try:
                chatbot_users = _cu_res.scalar() or 0
            finally:
                try:
                    _cu_res.close()
                except Exception:
                    pass

            return {
                "total_visitors": total_visitors,
                "visited_register": visited_register,
                "started_registration": started_registration,
                "completed_registration": completed_registration,
                "chatbot_users": chatbot_users,
                "conversion_rates": {
                    "visit_to_register_page": (
                        round(visited_register / total_visitors * 100, 2)
                        if total_visitors
                        else 0
                    ),
                    "register_page_to_start": (
                        round(started_registration / visited_register * 100, 2)
                        if visited_register
                        else 0
                    ),
                    "start_to_complete": (
                        round(completed_registration / started_registration * 100, 2)
                        if started_registration
                        else 0
                    ),
                    "overall_conversion": (
                        round(completed_registration / total_visitors * 100, 2)
                        if total_visitors
                        else 0
                    ),
                },
            }
        except Exception as e:
            logger.error(f"Error in _get_conversion_funnel: {e}")
            # Fallback to basic queries
            return self._get_conversion_funnel_fallback(start_date, end_date)

    def _get_conversion_funnel_fallback(
        self, start_date: datetime, end_date: datetime
    ) -> dict[str, Any]:
        """Fallback method for conversion funnel when optimized queries fail"""

        # Основни стъпки във funnel-а
        total_visitors = (
            self.db.query(func.count(func.distinct(UserBehavior.session_id)))
            .filter(UserBehavior.session_start.between(start_date, end_date))
            .scalar()
            or 0
        )

        # Посетили форма за регистрация
        visited_register = (
            self.db.query(func.count(func.distinct(AnalyticsEvent.user_session)))
            .filter(
                and_(
                    AnalyticsEvent.page_url.like("%register%"),
                    AnalyticsEvent.created_at.between(start_date, end_date),
                )
            )
            .scalar()
            or 0
        )

        # Започнали регистрация
        started_registration = (
            self.db.query(func.count(func.distinct(AnalyticsEvent.user_session)))
            .filter(
                and_(
                    AnalyticsEvent.event_action == "form_start",
                    AnalyticsEvent.event_category == "registration",
                    AnalyticsEvent.created_at.between(start_date, end_date),
                )
            )
            .scalar()
            or 0
        )

        # Завършили регистрация - използваме conversion_action в UserBehavior
        completed_registration = (
            self.db.query(func.count(UserBehavior.id))
            .filter(
                and_(
                    UserBehavior.conversion_action == "registration",
                    UserBehavior.session_start.between(start_date, end_date),
                )
            )
            .scalar()
            or 0
        )

        # Използвали чатбота
        chatbot_users = (
            self.db.query(func.count(func.distinct(ChatbotConversation.session_id)))
            .filter(ChatbotConversation.created_at.between(start_date, end_date))
            .scalar()
            or 0
        )

        return {
            "total_visitors": total_visitors,
            "visited_register": visited_register,
            "started_registration": started_registration,
            "completed_registration": completed_registration,
            "chatbot_users": chatbot_users,
            "conversion_rates": {
                "visit_to_register_page": (
                    round(visited_register / total_visitors * 100, 2)
                    if total_visitors
                    else 0
                ),
                "register_page_to_start": (
                    round(started_registration / visited_register * 100, 2)
                    if visited_register
                    else 0
                ),
                "start_to_complete": (
                    round(completed_registration / started_registration * 100, 2)
                    if started_registration
                    else 0
                ),
                "overall_conversion": (
                    round(completed_registration / total_visitors * 100, 2)
                    if total_visitors
                    else 0
                ),
            },
        }

    def _get_user_journey_analytics(
        self, start_date: datetime, end_date: datetime
    ) -> dict[str, Any]:
        """Анализ на потребителските пътища"""

        try:
            optimized_queries = DatabaseOptimizer.get_optimized_analytics_queries()

            # Най-чести entry points - uses optimized query with index
            entry_pages_query = optimized_queries["user_journey_entry_pages"]
            _entry_res = self.db.execute(
                text(entry_pages_query),
                {"start_date": start_date, "end_date": end_date},
            )
            try:
                entry_pages_rows = _entry_res.fetchall()
            finally:
                try:
                    _entry_res.close()
                except Exception:
                    pass

            entry_pages = [
                {"page": row[0], "entries": row[1]} for row in entry_pages_rows
            ]

            # Най-чести exit points - uses optimized query with index
            exit_pages_query = optimized_queries["user_journey_exit_pages"]
            _exit_res = self.db.execute(
                text(exit_pages_query), {"start_date": start_date, "end_date": end_date}
            )
            try:
                exit_pages_rows = _exit_res.fetchall()
            finally:
                try:
                    _exit_res.close()
                except Exception:
                    pass

            exit_pages = [{"page": row[0], "exits": row[1]} for row in exit_pages_rows]

            # Най-чести page sequences - optimized with reduced memory usage
            # Use a more efficient approach by limiting records and processing in batches
            common_paths = []
            sessions_with_sequences = (
                self.db.query(UserBehavior.pages_sequence)
                .filter(
                    and_(
                        UserBehavior.session_start.between(start_date, end_date),
                        UserBehavior.pages_sequence.isnot(None),
                    )
                )
                .limit(500)  # Reduced limit for better performance
                .all()
            )

            path_counter = Counter()
            for (pages_sequence,) in sessions_with_sequences:
                try:
                    sequence = json.loads(pages_sequence or "[]")
                    if len(sequence) >= 2:
                        # Simplify path extraction to reduce processing
                        path_parts = []
                        for page in sequence[:2]:  # Only first 2 pages for simplicity
                            url = page.get("url", "")
                            # Extract last part of URL or use "home"
                            path_part = url.split("/")[-1] or "home"
                            if path_part:
                                path_parts.append(path_part[:20])  # Limit length
                        if path_parts:
                            path = " → ".join(path_parts)
                            path_counter[path] += 1
                except Exception:
                    continue

            common_paths = [
                {"path": path, "count": count}
                for path, count in path_counter.most_common(10)
            ]

            return {
                "top_entry_pages": entry_pages,
                "top_exit_pages": exit_pages,
                "common_user_paths": common_paths,
            }
        except Exception as e:
            logger.error(f"Error in _get_user_journey_analytics: {e}")
            # Fallback to basic queries
            return self._get_user_journey_analytics_fallback(start_date, end_date)

    def _get_user_journey_analytics_fallback(
        self, start_date: datetime, end_date: datetime
    ) -> dict[str, Any]:
        """Fallback method for user journey analytics when optimized queries fail"""

        # Най-чести entry points
        entry_pages = (
            self.db.query(
                UserBehavior.entry_page, func.count(UserBehavior.id).label("entries")
            )
            .filter(
                and_(
                    UserBehavior.session_start.between(start_date, end_date),
                    UserBehavior.entry_page.isnot(None),
                )
            )
            .group_by(UserBehavior.entry_page)
            .order_by(func.count(UserBehavior.id).desc())
            .limit(10)
            .all()
        )

        # Най-чести exit points
        exit_pages = (
            self.db.query(
                UserBehavior.exit_page, func.count(UserBehavior.id).label("exits")
            )
            .filter(
                and_(
                    UserBehavior.session_start.between(start_date, end_date),
                    UserBehavior.exit_page.isnot(None),
                )
            )
            .group_by(UserBehavior.exit_page)
            .order_by(func.count(UserBehavior.id).desc())
            .limit(10)
            .all()
        )

        # Най-чести page sequences
        common_paths = []
        sessions_with_sequences = (
            self.db.query(UserBehavior)
            .filter(
                and_(
                    UserBehavior.session_start.between(start_date, end_date),
                    UserBehavior.pages_sequence.isnot(None),
                )
            )
            .all()
        )

        path_counter = Counter()
        for session in sessions_with_sequences:
            try:
                sequence = json.loads(session.pages_sequence or "[]")
                if len(sequence) >= 2:
                    path = " → ".join(
                        [page["url"].split("/")[-1] or "home" for page in sequence[:3]]
                    )
                    path_counter[path] += 1
            except Exception:
                continue

        common_paths = [
            {"path": path, "count": count}
            for path, count in path_counter.most_common(10)
        ]

        return {
            "top_entry_pages": [
                {"page": page, "entries": entries} for page, entries in entry_pages
            ],
            "top_exit_pages": [
                {"page": page, "exits": exits} for page, exits in exit_pages
            ],
            "common_user_paths": common_paths,
        }

    def _get_real_time_metrics(self) -> dict[str, Any]:
        """Real-time метрики (последният час)"""
        one_hour_ago = utc_now() - timedelta(hours=1)

        # Активни потребители (последните 30 минути)
        thirty_min_ago = utc_now() - timedelta(minutes=30)
        active_users = (
            self.db.query(func.count(func.distinct(UserBehavior.session_id)))
            .filter(UserBehavior.last_activity >= thirty_min_ago)
            .scalar()
            or 0
        )

        # Page views последният час
        recent_page_views = (
            self.db.query(AnalyticsEvent)
            .filter(
                and_(
                    AnalyticsEvent.event_type == "page_view",
                    AnalyticsEvent.created_at >= one_hour_ago,
                )
            )
            .count()
        )

        # Чатбот съобщения последният час
        recent_chatbot = (
            self.db.query(ChatbotConversation)
            .filter(ChatbotConversation.created_at >= one_hour_ago)
            .count()
        )

        return {
            "active_users_now": active_users,
            "page_views_last_hour": recent_page_views,
            "chatbot_messages_last_hour": recent_chatbot,
            "timestamp": utc_now().isoformat(),
        }

    def _is_cached(self, key: str, duration: int = None) -> bool:
        """Проверява дали данните са в кеша"""
        if duration is None:
            duration = self.cache_duration

        if key not in self._cache:
            return False

        cache_time = self._cache.get(f"{key}_timestamp", 0)
        return (time.time() - cache_time) < duration

    def _get_sample_analytics(self) -> dict[str, Any]:
        """Provide sample analytics data when database is empty"""
        return {
            "overview": {
                "unique_visitors": 1250,
                "total_page_views": 3450,
                "avg_session_time": 4.2,  # in minutes
                "bounce_rate": 35.5,
                "total_sessions": 2100,
                "conversions": 85,
                "conversion_rate": 4.0,
            },
            "user_engagement": {
                "top_pages": [
                    {"url": "/", "views": 1200},
                    {"url": "/help-request", "views": 850},
                    {"url": "/volunteer-signup", "views": 620},
                    {"url": "/about", "views": 480},
                    {"url": "/contact", "views": 300},
                ],
                "device_breakdown": [
                    {"device": "Desktop", "sessions": 1200},
                    {"device": "Mobile", "sessions": 780},
                    {"device": "Tablet", "sessions": 120},
                ],
                "hourly_activity": [
                    {
                        "hour": h,
                        "activity": max(5, int(50 * (1 + 0.5 * (h - 12) ** 2 / 144))),
                    }
                    for h in range(24)
                ],
            },
            "chatbot_analytics": {
                "total_conversations": 245,
                "response_types": {"ai": 180, "template": 45, "fallback": 20},
                "ai_statistics": {
                    "total_ai_responses": 180,
                    "avg_confidence": 0.82,
                    "avg_processing_time": 1.2,
                    "total_tokens": 12500,
                },
                "average_rating": 4.3,
                "rated_conversations": 95,
            },
            "performance_metrics": {
                "endpoint_performance": [
                    {"endpoint": "/api/chatbot", "avg_time": 1.2},
                    {"endpoint": "/api/requests", "avg_time": 0.8},
                    {"endpoint": "/admin/dashboard", "avg_time": 0.6},
                    {"endpoint": "/api/analytics", "avg_time": 1.8},
                ],
                "daily_performance": [
                    {
                        "date": (utc_now() - timedelta(days=i)).strftime("%Y-%m-%d"),
                        "avg_response_time": 1.0 + 0.1 * (i % 3),
                    }
                    for i in range(7)
                ],
            },
            "conversion_funnel": {
                "total_visitors": 1250,
                "visited_register": 320,
                "started_registration": 180,
                "completed_registration": 85,
                "chatbot_users": 245,
                "conversion_rates": {
                    "visit_to_register_page": 25.6,
                    "register_page_to_start": 56.3,
                    "start_to_complete": 47.2,
                    "overall_conversion": 6.8,
                },
            },
            "user_journey": {
                "top_entry_pages": [
                    {"page": "/", "entries": 680},
                    {"page": "/help-request", "entries": 320},
                    {"page": "/volunteer-signup", "entries": 180},
                    {"page": "/about", "entries": 70},
                ],
                "top_exit_pages": [
                    {"page": "/help-request", "exits": 450},
                    {"page": "/", "exits": 380},
                    {"page": "/contact", "exits": 220},
                    {"page": "/about", "exits": 150},
                ],
                "common_user_paths": [
                    {"path": "home → help-request → contact", "count": 85},
                    {"path": "home → volunteer-signup → contact", "count": 62},
                    {"path": "help-request → volunteer-signup", "count": 45},
                    {"path": "home → about → contact", "count": 38},
                ],
            },
            "real_time": {
                "active_users_now": 12,
                "page_views_last_hour": 45,
                "chatbot_messages_last_hour": 8,
                "timestamp": utc_now().isoformat(),
            },
            "is_sample_data": True,  # Flag to indicate this is sample data
        }


_real_analytics = None


def _minimal_analytics() -> dict:
    """Return a minimal non-demo analytics structure when data is unavailable."""
    return {
        "overview": {
            "unique_visitors": 0,
            "total_page_views": 0,
            "avg_session_time": 0.0,
            "bounce_rate": 0.0,
            "total_sessions": 0,
            "conversions": 0,
            "conversion_rate": 0.0,
        },
        "user_engagement": {
            "top_pages": [],
            "device_breakdown": [],
            "hourly_activity": [{"hour": h, "activity": 0} for h in range(24)],
        },
        "chatbot_analytics": {
            "total_conversations": 0,
            "response_types": {},
            "ai_statistics": {},
            "average_rating": 0,
            "rated_conversations": 0,
        },
        "performance_metrics": {"endpoint_performance": [], "daily_performance": []},
        "conversion_funnel": {
            "total_visitors": 0,
            "visited_register": 0,
            "started_registration": 0,
            "completed_registration": 0,
            "chatbot_users": 0,
            "conversion_rates": {},
        },
        "user_journey": {
            "top_entry_pages": [],
            "top_exit_pages": [],
            "common_user_paths": [],
        },
        "real_time": {
            "active_users_now": 0,
            "page_views_last_hour": 0,
            "chatbot_messages_last_hour": 0,
            "timestamp": utc_now().isoformat(),
        },
        "data_availability": {
            "available": False,
            "reason": "Analytics service is unavailable or has not been initialized.",
            "confidence": "low",
            "source_tables": [
                "analytics_events",
                "user_behaviors",
                "chatbot_conversations",
            ],
            "last_updated": utc_now().isoformat(),
        },
        "is_sample_data": False,
    }


class _NoopAnalytics:
    def get_dashboard_analytics(self, *a, **k):
        return _minimal_analytics()

    def track_event(self, *a, **k):
        return False


class _LazyAnalytics:
    """Lazy wrapper that delegates to a real analytics service if available,
    otherwise returns no-op data when running in TESTING mode or when not initialized.
    """

    def get_dashboard_analytics(self, *a, **k):
        try:
            from flask import current_app

            if current_app and current_app.config.get("TESTING"):
                return _NoopAnalytics().get_dashboard_analytics(*a, **k)
        except Exception:
            # No app context or other issue -> fall back to noop if real not set
            pass

        if _real_analytics is None:
            return _NoopAnalytics().get_dashboard_analytics(*a, **k)

        return _real_analytics.get_dashboard_analytics(*a, **k)

    def track_event(self, *a, **k):
        try:
            from flask import current_app

            if current_app and current_app.config.get("TESTING"):
                return _NoopAnalytics().track_event(*a, **k)
        except Exception:
            pass

        if _real_analytics is None:
            return _NoopAnalytics().track_event(*a, **k)

        return _real_analytics.track_event(*a, **k)


# Expose a lazy analytics_service instance for importers to use safely.
analytics_service = _LazyAnalytics()


def init_analytics_service(db_session):
    """Initialize the analytics service with a database session and set the real implementation."""
    global _real_analytics
    _real_analytics = AdvancedAnalytics(db_session)
    return _real_analytics
