import asyncio
import json
import os
import time
from datetime import datetime, timedelta
from typing import Optional

try:
    import aiofiles
except Exception:
    aiofiles = None

try:
    import httpx
except Exception:
    httpx = None
from flask import (
    Blueprint,
    Response,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.future import select
from sqlalchemy.orm import sessionmaker

try:
    from .extensions import db
except Exception:
    from backend.extensions import db

try:
    from .permissions import require_admin_login
except Exception:
    from permissions import require_admin_login

analytics_bp = Blueprint("analytics_bp", __name__)

# Import models (use canonical top-level models module)
try:
    from .models import HelpRequest, Volunteer
except Exception:
    try:
        from backend.models import HelpRequest, Volunteer
    except Exception:
        # Fallback for standalone execution
        HelpRequest = None
        Volunteer = None

# Import caching decorators from performance optimization
try:
    from performance_optimization import AnalyticsCache

    # Don't create cache instance here - get it from app context
    cached_analytics_data = None
    cached_analytics_data_1min = None
    cached_analytics_data_5min = None
except ImportError:
    # Fallback if caching not available
    def cached_analytics_data(f):
        return f

    def cached_analytics_data_1min(f):
        return f

    def cached_analytics_data_5min(f):
        return f

    cache = None


def get_cached_decorator(timeout=None):
    """Get cached decorator from app context"""
    try:
        from flask import current_app

        if hasattr(current_app, "analytics_cache"):
            cache = current_app.analytics_cache
            return cache.cached_analytics_data(timeout)
        else:
            # Fallback - return identity function
            def identity(f):
                return f

            return identity
    except Exception:
        # Fallback - return identity function
        def identity(f):
            return f

        return identity


def _parse_period_args():
    """Extract and normalize date filters from the incoming request."""

    def _safe_parse(value):
        if not value:
            return None
        try:
            return datetime.strptime(value, "%Y-%m-%d")
        except (ValueError, TypeError):
            return None

    start_param = request.args.get("start_date")
    end_param = request.args.get("end_date")
    days_param = request.args.get("days")

    start_dt = _safe_parse(start_param)
    end_dt = _safe_parse(end_param)

    if start_dt and end_dt and start_dt > end_dt:
        start_dt, end_dt = end_dt, start_dt

    try:
        days = int(days_param) if days_param and days_param.isdigit() else None
    except (TypeError, ValueError):
        days = None

    if start_dt and end_dt:
        computed_days = max(1, (end_dt.date() - start_dt.date()).days + 1)
        days = computed_days

    if days is None:
        days = 30

    days = max(1, min(days, 365))
    return days, start_dt, end_dt


def _format_filter_summary(
    days: int, start_dt: datetime | None, end_dt: datetime | None
) -> str:
    """Create a localized summary for the currently active date filters."""

    if start_dt and end_dt:
        start_label = start_dt.strftime("%d.%m.%Y")
        end_label = end_dt.strftime("%d.%m.%Y")
        return f"Показваме данни от {start_label} до {end_label} ({days} дни)"

    if days == 1:
        return "Показваме данни за последния ден"

    return f"Показваме данни за последните {days} дни"


# Create decorators that will use app context
def cached_analytics_data_5min_decorator(f):
    return get_cached_decorator(300)(f)


def cached_analytics_data_1min_decorator(f):
    return get_cached_decorator(60)(f)


@analytics_bp.route("/")
def analytics_page():
    # DEBUG: Log session state in analytics route
    print(f"DEBUG analytics_page: session keys: {list(session.keys())}")
    print(f"DEBUG analytics_page: admin_logged_in: {session.get('admin_logged_in')}")
    print(f"DEBUG analytics_page: session id: {id(session)}")
    print(f"DEBUG analytics_page: current_app: {current_app}")
    # Redirect to admin analytics if user is admin, otherwise to login
    if session.get("admin_logged_in"):
        return redirect(url_for("analytics_bp.admin_analytics"))
    else:
        flash("Аналитиката е достъпна само за администратори.", "info")
        return redirect(url_for("admin_login"))


@analytics_bp.route("/api/analytics/data")
# @require_admin_login  # Temporarily disabled for testing
@cached_analytics_data_5min_decorator  # Cache for 5 minutes
async def analytics_data():
    try:
        # Check if simple data is requested
        simple = request.args.get("simple", "false").lower() == "true"

        if simple:
            # Return simple dashboard stats using async database queries
            try:
                from flask import current_app
                from sqlalchemy import func, select

                from appy import async_session

                async with async_session() as session:
                    # Count HelpRequest
                    _res = await session.execute(select(func.count(HelpRequest.id)))
                    try:
                        total_requests = _res.scalar()
                    finally:
                        try:
                            _res.close()
                        except Exception:
                            pass

                    # Count Volunteer
                    _res = await session.execute(select(func.count(Volunteer.id)))
                    try:
                        total_volunteers = _res.scalar()
                    finally:
                        try:
                            _res.close()
                        except Exception:
                            pass

                    # Count active tasks
                    try:
                        from backend.models_with_analytics import Task

                        _res = await session.execute(
                            select(func.count(Task.id)).where(
                                Task.status.in_(["assigned", "in_progress"])
                            )
                        )
                        try:
                            active_tasks = _res.scalar()
                        finally:
                            try:
                                _res.close()
                            except Exception:
                                pass
                    except (ImportError, AttributeError):
                        # If Task model not available, count from HelpRequest status
                        _res = await session.execute(
                            select(func.count(HelpRequest.id)).where(
                                HelpRequest.status.in_(["assigned", "in_progress"])
                            )
                        )
                        try:
                            active_tasks = _res.scalar()
                        finally:
                            try:
                                _res.close()
                            except Exception:
                                pass

                return jsonify(
                    {
                        "total_requests": total_requests,
                        "total_volunteers": total_volunteers,
                        "active_tasks": active_tasks,
                    }
                )
            except Exception as e:
                print(f"Error getting simple analytics data: {e}")
                return jsonify(
                    {"total_requests": 0, "total_volunteers": 0, "active_tasks": 0}
                )

        # Return full analytics data
        try:
            from backend.analytics_service import analytics_service
        except ImportError:
            from backend.analytics_service import analytics_service

        # Run analytics_service in thread executor since it may not be async
        import asyncio

        loop = asyncio.get_event_loop()
        days, start_dt, end_dt = _parse_period_args()

        data = await loop.run_in_executor(
            None,
            lambda: analytics_service.get_dashboard_analytics(
                days=days, start_date=start_dt, end_date=end_dt
            ),
        )

        # Check if response should be compressed
        accept_encoding = request.headers.get("Accept-Encoding", "")
        if "gzip" in accept_encoding and len(str(data)) > 1024:  # Compress if > 1KB
            try:
                from performance_optimization import APIOptimizer

                compressed_data = APIOptimizer.compress_response(data)
                response = Response(compressed_data, mimetype="application/json")
                response.headers["Content-Encoding"] = "gzip"
                response.headers["Vary"] = "Accept-Encoding"
                return response
            except Exception as compress_error:
                print(f"Compression failed, returning uncompressed: {compress_error}")
                return jsonify(data)
        else:
            return jsonify(data)
    except Exception as e:
        print(f"Error getting analytics data: {type(e).__name__}")
        # Fallback to basic stats
        try:
            try:
                from admin_analytics import AnalyticsEngine
            except ImportError:
                from admin_analytics import AnalyticsEngine

            # Run in thread executor
            import asyncio

            loop = asyncio.get_event_loop()
            days, start_dt, end_dt = _parse_period_args()
            data = await loop.run_in_executor(
                None,
                lambda: AnalyticsEngine.get_dashboard_stats(
                    days=days, start_date=start_dt, end_date=end_dt
                ),
            )
            return jsonify(data)
        except Exception as fallback_e:
            print(f"Fallback analytics also failed: {type(fallback_e).__name__}")
            return jsonify({"error": "Analytics not available"})


@analytics_bp.route("/api/analytics/simple")
# @require_admin_login  # Temporarily disabled for testing
async def analytics_simple_data():
    """Simple analytics endpoint returning basic stats for dashboard"""
    print("DEBUG: analytics_simple_data function called")
    try:
        # Check cache first
        try:
            from flask import current_app

            if hasattr(current_app, "analytics_cache") and current_app.analytics_cache:
                cache = current_app.analytics_cache
                cache_key = "analytics_simple_data"
                cached_result = cache.cache.get(cache_key)
                if cached_result is not None:
                    print(f"Cache HIT for {cache_key}")
                    return cached_result
                print(f"Cache MISS for {cache_key}")
        except Exception as cache_error:
            print(f"Cache error: {cache_error}")

        # Get basic counts from database using async session
        from flask import current_app
        from sqlalchemy import func, select

        from appy import async_session

        async with async_session() as session:
            # Count HelpRequest
            _res = await session.execute(select(func.count(HelpRequest.id)))
            try:
                total_requests = _res.scalar()
            finally:
                try:
                    _res.close()
                except Exception:
                    pass

            # Count Volunteer
            _res = await session.execute(select(func.count(Volunteer.id)))
            try:
                total_volunteers = _res.scalar()
            finally:
                try:
                    _res.close()
                except Exception:
                    pass

            # Count active tasks
            try:
                from backend.models_with_analytics import Task

                _res = await session.execute(
                    select(func.count(Task.id)).where(
                        Task.status.in_(["assigned", "in_progress"])
                    )
                )
                try:
                    active_tasks = _res.scalar()
                finally:
                    try:
                        _res.close()
                    except Exception:
                        pass
            except (ImportError, AttributeError):
                # If Task model not available, count from HelpRequest status
                _res = await session.execute(
                    select(func.count(HelpRequest.id)).where(
                        HelpRequest.status.in_(["assigned", "in_progress"])
                    )
                )
                try:
                    active_tasks = _res.scalar()
                finally:
                    try:
                        _res.close()
                    except Exception:
                        pass

        result = jsonify(
            {
                "total_requests": total_requests,
                "total_volunteers": total_volunteers,
                "active_tasks": active_tasks,
            }
        )

        # Cache the result
        try:
            if hasattr(current_app, "analytics_cache") and current_app.analytics_cache:
                cache = current_app.analytics_cache
                cache.cache.set(cache_key, result, timeout=300)  # 5 minutes
                print(f"Cached result for {cache_key}")
        except Exception as cache_error:
            print(f"Cache write error: {cache_error}")

        return result

    except Exception as e:
        print(f"Error getting simple analytics data: {e}")
        return jsonify({"total_requests": 0, "total_volunteers": 0, "active_tasks": 0})


def _bookmarks_path():
    path = os.path.join(current_app.instance_path, "analytics_bookmarks.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as f:
            json.dump([], f)
    return path


@analytics_bp.route("/bookmarks", methods=["GET", "POST", "DELETE"])
def analytics_bookmarks():
    path = _bookmarks_path()
    if request.method == "GET":
        with open(path, encoding="utf-8") as f:
            return jsonify(json.load(f))
    if request.method == "POST":
        payload = request.get_json(force=True)
        with open(path, "r+", encoding="utf-8") as f:
            data = json.load(f)
            data.append(payload)
            f.seek(0)
            f.truncate()
            json.dump(data, f, ensure_ascii=False, indent=2)
        return jsonify(payload), 201
    if request.method == "DELETE":
        name = request.args.get("name")
        with open(path, "r+", encoding="utf-8") as f:
            data = json.load(f)
            data = [b for b in data if b.get("name") != name]
            f.seek(0)
            f.truncate()
            json.dump(data, f, ensure_ascii=False, indent=2)
        return jsonify({"deleted": name}), 200


@analytics_bp.route("/stream")
def analytics_stream():
    """Server-Sent Events endpoint for real-time analytics updates"""

    def generate():
        """Generator function for SSE stream"""
        try:
            # Send initial connection message
            yield f"data: {json.dumps({'type': 'connected', 'message': 'Real-time analytics stream connected'})}\n\n"

            last_check = datetime.now()
            alert_check_interval = timedelta(
                seconds=30
            )  # Check for alerts every 30 seconds
            stats_update_interval = timedelta(
                seconds=10
            )  # Update stats every 10 seconds

            while True:
                current_time = datetime.now()

                # Check for new alerts
                if current_time - last_check >= alert_check_interval:
                    try:
                        from advanced_analytics import AdvancedAnalytics

                        analytics = AdvancedAnalytics()

                        # Get current analytics data for alert checking
                        anomalies = analytics.detect_anomalies(timeframe_days=1)
                        insights = analytics.generate_insights_report()

                        analytics_data = {
                            "anomalies": anomalies,
                            "error_rate": 0,  # Would need to calculate from actual error events
                            "active_users": insights.get("user_segments", {})
                            .get("regular_users", {})
                            .get("count", 0),
                            "trends": insights.get("kpi_trends", {}).get("trends", {}),
                        }

                        triggered_alerts = alert_system.check_alerts(analytics_data)

                        if triggered_alerts:
                            for alert in triggered_alerts:
                                yield f"data: {json.dumps({'type': 'alert', 'data': alert})}\n\n"

                        last_check = current_time

                    except Exception as e:
                        print(f"Error checking alerts in stream: {e}")

                # Send periodic stats updates
                try:
                    from analytics_service import analytics_service

                    data = analytics_service.get_dashboard_analytics()

                    # Extract live stats
                    overview = data.get("overview", {})
                    live_data = {
                        "type": "stats_update",
                        "data": {
                            "requests_today": overview.get("total_page_views", 0),
                            "volunteers_active": overview.get("unique_visitors", 0),
                            "conversions_today": overview.get("conversions", 0),
                            "avg_response_time": overview.get("avg_session_time", 0),
                            "timestamp": int(time.time()),
                        },
                    }
                    yield f"data: {json.dumps(live_data)}\n\n"

                except Exception as e:
                    print(f"Error getting live stats in stream: {e}")

                # Wait before next iteration
                time.sleep(10)  # Send updates every 10 seconds

        except GeneratorExit:
            # Client disconnected
            print("SSE client disconnected")
        except Exception as e:
            print(f"SSE stream error: {e}")
            yield f"data: {json.dumps({'type': 'error', 'message': 'Stream error occurred'})}\n\n"

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "Access-Control-Allow-Origin": "*",
        },
    )


@analytics_bp.route("/api/analytics/live")
@require_admin_login
@cached_analytics_data_1min_decorator  # Cache for 1 minute for live data
async def analytics_live():
    """Get live analytics data for real-time updates"""
    try:
        import asyncio

        from analytics_service import analytics_service

        # Run analytics_service in thread executor since it may not be async
        loop = asyncio.get_event_loop()
        data = await loop.run_in_executor(
            None, analytics_service.get_dashboard_analytics
        )

        # Extract live stats from overview
        overview = data.get("overview", {})
        live_data = {
            "requests_today": overview.get("total_page_views", 0),
            "volunteers_active": overview.get("unique_visitors", 0),
            "conversions_today": overview.get("conversions", 0),
            "avg_response_time": overview.get("avg_session_time", 0),
            "timestamp": int(time.time()),
        }
        return jsonify(live_data)
    except Exception as e:
        print(f"Error getting live analytics: {type(e).__name__}")
        return jsonify({"error": "Live analytics not available"})


@analytics_bp.route("/api/analytics/trends")
@require_admin_login
async def analytics_trends():
    """Get trend data for charts"""
    try:
        months = int(request.args.get("months", 6))
        demo_mode = bool(current_app.config.get("DEMO_MODE")) if current_app else False

        if not demo_mode:
            return jsonify(
                {
                    "labels": [],
                    "requests": [],
                    "completed": [],
                    "volunteers": [],
                    "available": False,
                    "reason": "Historical trend queries are not implemented for this endpoint.",
                    "confidence": "low",
                    "source_tables": ["help_requests", "volunteers"],
                    "last_updated": datetime.now().isoformat(),
                }
            )

        labels = []
        requests = []
        completed = []
        volunteers = []

        for i in range(months):
            date = datetime.datetime.now() - datetime.timedelta(days=30 * i)
            labels.append(date.strftime("%Y-%m"))
            requests.append(100 + i * 10)
            completed.append(80 + i * 8)
            volunteers.append(50 + i * 5)

        trend_data = {
            "labels": labels[::-1],  # Reverse to show chronological order
            "requests": requests[::-1],
            "completed": completed[::-1],
            "volunteers": volunteers[::-1],
        }
        return jsonify(trend_data)
    except Exception as e:
        print(f"Error getting trend analytics: {type(e).__name__}")
        return jsonify({"error": "Trend analytics not available"})


@analytics_bp.route("/admin_analytics", methods=["GET"])
# @require_admin_login  # Temporarily disabled for testing
def admin_analytics():
    """Analytics dashboard - professional template"""
    try:
        unavailable_dashboard_stats = {
            "totals": {
                "requests": 0,
                "volunteers": 0,
            },
            "overview": {
                "total_page_views": 0,
                "conversion_rate": 0,
                "avg_session_time": 0,
                "unique_visitors": 0,
                "bounce_rate": 0,
            },
            "performance_metrics": {"endpoint_performance": []},
            "chatbot_analytics": {
                "total_conversations": 0,
                "average_rating": 0,
            },
            "available": False,
            "reason": "Dashboard analytics source query did not return data.",
            "confidence": "low",
            "source_tables": ["help_requests", "volunteers"],
            "last_updated": datetime.now().isoformat(),
        }
        unavailable_performance_metrics = {
            "success_rate": None,
            "utilization_rate": None,
            "completed_requests": 0,
            "active_requests": 0,
            "active_volunteers": 0,
            "available": False,
            "reason": "Performance metric source query did not return data.",
            "confidence": "low",
            "source_tables": ["help_requests", "volunteers"],
            "last_updated": datetime.now().isoformat(),
        }
        unavailable_predictions = {
            "labels": [],
            "requests_predicted": [],
            "volunteers_predicted": [],
            "available": False,
            "reason": "Prediction model output is unavailable.",
            "confidence": "low",
            "source_tables": ["help_requests", "volunteers"],
            "last_updated": datetime.now().isoformat(),
        }
        unavailable_trends_data = {
            "labels": [],
            "requests": [],
            "completed": [],
            "volunteers": [],
            "available": False,
            "reason": "Historical trend query did not return data.",
            "confidence": "low",
            "source_tables": ["help_requests", "volunteers"],
            "last_updated": datetime.now().isoformat(),
        }
        unavailable_category_stats = {
            "categories": [],
            "counts": [],
            "available": False,
            "reason": "Category aggregation did not return data.",
            "confidence": "low",
            "source_tables": ["help_requests"],
            "last_updated": datetime.now().isoformat(),
        }

        try:
            from admin_analytics import AnalyticsEngine
        except ImportError:  # pragma: no cover - fallback for modular imports
            AnalyticsEngine = None

        try:
            from analytics_service import analytics_service
        except ImportError:
            analytics_service = None

        days, start_dt, end_dt = _parse_period_args()
        logger = current_app.logger if current_app else None

        dashboard_stats = unavailable_dashboard_stats
        performance_metrics = unavailable_performance_metrics
        predictions = unavailable_predictions
        recommendations = []
        category_stats = unavailable_category_stats
        trends_data = unavailable_trends_data
        geo_data = {"requests": [], "volunteers": []}
        live_stats = {}
        advanced_analytics = {}
        anomalies = []

        if AnalyticsEngine:
            try:
                dashboard_stats = AnalyticsEngine.get_dashboard_stats(
                    days=days, start_date=start_dt, end_date=end_dt
                )
            except Exception as analytics_error:
                if logger:
                    logger.warning(
                        "Dashboard analytics data unavailable: %s", analytics_error
                    )
            else:
                live_stats = dashboard_stats.get("real_time", {}) or {}

                raw_category_stats = dashboard_stats.get("category_stats", {}) or {}
                if raw_category_stats:
                    category_stats = {
                        "categories": list(raw_category_stats.keys()),
                        "counts": list(raw_category_stats.values()),
                    }

                perf_metrics = dashboard_stats.get("performance_metrics")
                if perf_metrics:
                    performance_metrics = perf_metrics

                daily_stats = dashboard_stats.get("daily_stats") or []
                if daily_stats:
                    trends_data = {
                        "labels": [item.get("date", "") for item in daily_stats],
                        "requests": [item.get("requests", 0) for item in daily_stats],
                        "completed": [
                            item.get("completed", item.get("requests", 0))
                            for item in daily_stats
                        ],
                        "volunteers": [
                            item.get("volunteers", 0) for item in daily_stats
                        ],
                    }

                if not any(trends_data.get("requests", [])):
                    try:
                        trends_data = AnalyticsEngine.get_trends_data(months=12)
                    except Exception as trend_error:
                        if logger:
                            logger.debug("Trend data fallback failed: %s", trend_error)

                try:
                    predictions = AnalyticsEngine.get_predictions(months=3)
                except Exception as prediction_error:
                    if logger:
                        logger.debug(
                            "Prediction generation failed: %s", prediction_error
                        )

                try:
                    geo_data = AnalyticsEngine.get_geo_data()
                except Exception as geo_error:
                    if logger:
                        logger.debug("Geo data generation failed: %s", geo_error)

        if analytics_service:
            try:
                advanced_analytics = analytics_service.get_dashboard_analytics(
                    days=days, start_date=start_dt, end_date=end_dt
                )
            except Exception as advanced_error:
                if logger:
                    logger.debug("Advanced analytics unavailable: %s", advanced_error)
                advanced_analytics = {}
            else:
                if isinstance(advanced_analytics, dict):
                    anomalies = advanced_analytics.get("anomalies", []) or []

        filter_summary = _format_filter_summary(days, start_dt, end_dt)

        return render_template(
            "admin_analytics_professional.html",
            dashboard_stats=dashboard_stats,
            performance_metrics=performance_metrics,
            anomalies=anomalies,
            predictions=predictions,
            recommendations=recommendations,
            trends_data=trends_data,
            category_stats=category_stats,
            geo_data=geo_data,
            live_stats=live_stats,
            advanced_analytics=advanced_analytics,
            filter_summary=filter_summary,
            filters_context={
                "days": days,
                "start_date": start_dt.isoformat() if start_dt else None,
                "end_date": end_dt.isoformat() if end_dt else None,
            },
        )

    except Exception as e:  # pragma: no cover - safeguard
        if current_app:
            current_app.logger.error(
                "Error loading analytics dashboard: %s", e, exc_info=True
            )
        else:
            print(f"Error loading analytics dashboard: {type(e).__name__}: {e}")
        return "Error loading analytics dashboard", 500


@analytics_bp.route("/test_analytics", methods=["GET"])
def test_analytics():
    """Test route to verify blueprint is working"""
    return "Analytics blueprint is working!"


@analytics_bp.route("/api/analytics/test_simple")
def test_simple_analytics():
    """Test endpoint for simple analytics"""
    print("DEBUG: test_simple_analytics called")
    simple_param = request.args.get("simple", "false")
    print(f"DEBUG: simple_param = '{simple_param}'")
    simple = simple_param.lower() == "true"
    print(f"DEBUG: simple = {simple}")

    return jsonify(
        {
            "simple_param": simple_param,
            "simple": simple,
            "message": "test endpoint working",
        }
    )


# Predictive Analytics Routes


@analytics_bp.route("/predictive-analytics")
def predictive_analytics_page():
    """Predictive analytics dashboard page"""
    if not session.get("admin_logged_in"):
        flash("Предиктивната аналитика е достъпна само за администратори.", "info")
        return redirect(url_for("admin_login"))

    return render_template("predictive_analytics.html")


@analytics_bp.route("/api/predictive/regional-demand")
@require_admin_login
async def predictive_regional_demand():
    """Get regional demand forecast"""
    try:
        import asyncio

        region = request.args.get("region")
        days_ahead = int(request.args.get("days", 7))

        try:
            from .predictive_analytics import predictive_analytics
        except ImportError:
            return jsonify(
                {
                    "error": "Predictive analytics not available",
                    "details": "Import failed",
                }
            )

        # Run predictive analytics in thread executor
        loop = asyncio.get_event_loop()
        data = await loop.run_in_executor(
            None, predictive_analytics.get_regional_demand_forecast, region, days_ahead
        )
        return jsonify(data)

    except Exception as e:
        print(f"Error getting regional demand forecast: {type(e).__name__}")
        return jsonify({"error": "Failed to get regional demand forecast"})


@analytics_bp.route("/api/predictive/workload")
@require_admin_login
async def predictive_workload():
    """Get workload prediction"""
    try:
        import asyncio

        hours_ahead = int(request.args.get("hours", 24))

        try:
            from .predictive_analytics import predictive_analytics
        except ImportError:
            return jsonify(
                {
                    "error": "Predictive analytics not available",
                    "details": "Import failed",
                }
            )

        # Run predictive analytics in thread executor
        loop = asyncio.get_event_loop()
        data = await loop.run_in_executor(
            None, predictive_analytics.get_workload_prediction, hours_ahead
        )
        return jsonify(data)

    except Exception as e:
        print(f"Error getting workload prediction: {type(e).__name__}")
        return jsonify({"error": "Failed to get workload prediction"})


@analytics_bp.route("/api/predictive/insights")
@require_admin_login
async def predictive_insights():
    """Get predictive insights and recommendations"""
    try:
        import asyncio

        try:
            from .predictive_analytics import predictive_analytics
        except ImportError:
            return jsonify(
                {
                    "error": "Predictive analytics not available",
                    "details": "Import failed",
                }
            )

        # Run predictive analytics in thread executor
        loop = asyncio.get_event_loop()
        data = await loop.run_in_executor(
            None, predictive_analytics.get_predictive_insights
        )
        return jsonify(data)

    except Exception as e:
        print(f"Error getting predictive insights: {type(e).__name__}")
        return jsonify({"error": "Failed to get predictive insights"})


@analytics_bp.route("/api/predictive/model-info")
@require_admin_login
async def predictive_model_info():
    """Get information about predictive models"""
    try:
        import asyncio

        try:
            from .predictive_analytics import predictive_analytics
        except ImportError:
            return jsonify(
                {
                    "error": "Predictive analytics not available",
                    "details": "Import failed",
                }
            )

        # Get sample predictions to show model capabilities
        loop = asyncio.get_event_loop()
        regional_sample = await loop.run_in_executor(
            None, predictive_analytics.get_regional_demand_forecast, None, 1
        )
        workload_sample = await loop.run_in_executor(
            None, predictive_analytics.get_workload_prediction, 1
        )

        model_info = {
            "regional_demand_model": {
                "type": "Random Forest Regression",
                "features": [
                    "day_of_week",
                    "month",
                    "season",
                    "historical_avg",
                    "trend_factor",
                    "volunteer_density",
                    "population_density",
                ],
                "prediction_horizon": "1-30 days",
                "accuracy_metrics": regional_sample.get("model_info", {}).get(
                    "accuracy", "N/A"
                ),
                "last_trained": regional_sample.get("generated_at", "N/A"),
            },
            "workload_prediction_model": {
                "type": "Gradient Boosting Regression",
                "features": [
                    "current_requests",
                    "active_volunteers",
                    "avg_response_time",
                    "day_of_week",
                    "hour_of_day",
                    "season",
                ],
                "prediction_horizon": "1-168 hours",
                "accuracy_metrics": workload_sample.get("model_info", {}).get(
                    "accuracy", "N/A"
                ),
                "last_trained": workload_sample.get("generated_at", "N/A"),
            },
            "data_sources": [
                "HelpRequest table (historical patterns)",
                "Volunteer table (capacity data)",
                "UserActivity table (engagement patterns)",
                "Real-time system metrics",
            ],
            "update_frequency": "Real-time predictions with 1-hour cache",
            "fallback_strategy": "Rule-based heuristics when ML models unavailable",
        }

        return jsonify(model_info)

    except Exception as e:
        print(f"Error getting model info: {type(e).__name__}")
        return jsonify({"error": "Failed to get model information"})


# Advanced Analytics Routes


@analytics_bp.route("/api/advanced/anomalies")
@require_admin_login
def get_anomalies():
    """Get detected anomalies in analytics data"""
    try:
        from advanced_analytics import AdvancedAnalytics

        analytics = AdvancedAnalytics()
        timeframe_days = int(request.args.get("days", 7))

        # Run anomaly detection synchronously
        anomalies = analytics.detect_anomalies(timeframe_days)

        return jsonify(
            {
                "anomalies": anomalies,
                "timeframe_days": timeframe_days,
                "total_anomalies": len(anomalies),
                "generated_at": datetime.now().isoformat(),
            }
        )

    except Exception as e:
        print(f"Error getting anomalies: {type(e).__name__}")
        return jsonify({"error": "Failed to detect anomalies"})


@analytics_bp.route("/api/advanced/predictions")
@require_admin_login
async def get_predictions():
    """Get predictive analytics for user behavior"""
    try:
        import asyncio

        from advanced_analytics import AdvancedAnalytics

        analytics = AdvancedAnalytics()
        user_id = request.args.get("user_id")

        # Run prediction in thread executor
        loop = asyncio.get_event_loop()
        predictions = await loop.run_in_executor(
            None, analytics.predict_user_behavior, user_id or None
        )

        return jsonify(
            {
                "predictions": predictions,
                "user_id": user_id,
                "generated_at": datetime.now().isoformat(),
            }
        )

    except Exception as e:
        print(f"Error getting predictions: {type(e).__name__}")
        return jsonify({"error": "Failed to generate predictions"})


@analytics_bp.route("/api/advanced/insights")
@require_admin_login
async def get_insights():
    """Get comprehensive analytics insights report"""
    try:
        import asyncio

        from advanced_analytics import AdvancedAnalytics

        analytics = AdvancedAnalytics()

        # Run insights generation in thread executor
        loop = asyncio.get_event_loop()
        report = await loop.run_in_executor(None, analytics.generate_insights_report)

        return jsonify(report)

    except Exception as e:
        print(f"Error getting insights: {type(e).__name__}")
        return jsonify({"error": "Failed to generate insights"})


@analytics_bp.route("/api/advanced/user-behavior")
# @require_admin_login  # Disabled since called from authenticated admin dashboard
async def get_user_behavior():
    """Get user behavior analysis"""
    try:
        import asyncio

        from advanced_analytics import AdvancedAnalytics

        analytics = AdvancedAnalytics()
        days = int(request.args.get("days", 30)) if request else 30

        # Run user analysis operations in thread executor
        loop = asyncio.get_event_loop()
        segments = await loop.run_in_executor(None, analytics._segment_users)
        trends = await loop.run_in_executor(None, analytics._analyze_kpi_trends)
        recent_events = await loop.run_in_executor(
            None, analytics.get_recent_events, days
        )

        user_activity = {}

        for event in recent_events:
            if event["user_id"]:
                if event["user_id"] not in user_activity:
                    user_activity[event["user_id"]] = {
                        "events_count": 0,
                        "last_activity": event["timestamp"],
                        "event_types": set(),
                        "pages_visited": set(),
                    }

                user_activity[event["user_id"]]["events_count"] += 1
                user_activity[event["user_id"]]["event_types"].add(event["event_type"])
                if event["page_url"]:
                    user_activity[event["user_id"]]["pages_visited"].add(
                        event["page_url"]
                    )

                # Update last activity if more recent
                if (
                    event["timestamp"]
                    > user_activity[event["user_id"]]["last_activity"]
                ):
                    user_activity[event["user_id"]]["last_activity"] = event[
                        "timestamp"
                    ]

        # Convert sets to lists for JSON serialization
        for user_id, data in user_activity.items():
            data["event_types"] = list(data["event_types"])
            data["pages_visited"] = list(data["pages_visited"])
            data["last_activity"] = data["last_activity"].isoformat()

        return jsonify(
            {
                "user_segments": segments,
                "kpi_trends": trends,
                "user_activity": user_activity,
                "analysis_period_days": days,
                "total_users_analyzed": len(user_activity),
                "generated_at": datetime.now().isoformat(),
            }
        )

    except Exception as e:
        print(f"Error getting user behavior: {type(e).__name__}")
        return jsonify({"error": "Failed to analyze user behavior"})


# Custom Alerts System


class AlertSystem:
    """Custom alerts system for analytics"""

    def __init__(self):
        self.alerts = []
        self.load_alerts()

    def load_alerts(self):
        """Load alerts configuration"""
        # Default alerts configuration
        self.alerts = [
            {
                "id": "traffic_spike",
                "name": "Traffic Spike Alert",
                "description": "Alert when traffic increases by more than 50%",
                "type": "anomaly",
                "threshold": 50,
                "enabled": True,
                "notification_channels": ["dashboard", "email"],
                "cooldown_minutes": 60,
            },
            {
                "id": "error_rate_high",
                "name": "High Error Rate Alert",
                "description": "Alert when error rate exceeds 10%",
                "type": "threshold",
                "threshold": 10,
                "enabled": True,
                "notification_channels": ["dashboard", "email"],
                "cooldown_minutes": 30,
            },
            {
                "id": "conversion_drop",
                "name": "Conversion Rate Drop",
                "description": "Alert when conversion rate drops by more than 20%",
                "type": "trend",
                "threshold": -20,
                "enabled": True,
                "notification_channels": ["dashboard"],
                "cooldown_minutes": 120,
            },
            {
                "id": "user_engagement_low",
                "name": "Low User Engagement",
                "description": "Alert when daily active users drop below threshold",
                "type": "threshold",
                "threshold": 10,  # Minimum daily active users
                "enabled": False,
                "notification_channels": ["dashboard"],
                "cooldown_minutes": 1440,  # 24 hours
            },
        ]

    def check_alerts(self, analytics_data):
        """Check if any alerts should be triggered"""
        triggered_alerts = []

        for alert in self.alerts:
            if not alert.get("enabled", False):
                continue

            if self._should_trigger_alert(alert, analytics_data):
                triggered_alerts.append(
                    {
                        "alert_id": alert["id"],
                        "name": alert["name"],
                        "description": alert["description"],
                        "severity": self._get_alert_severity(alert),
                        "triggered_at": datetime.now().isoformat(),
                        "data": analytics_data,
                    }
                )

        return triggered_alerts

    def _should_trigger_alert(self, alert, analytics_data):
        """Check if alert conditions are met"""
        alert_type = alert.get("type")
        threshold = alert.get("threshold", 0)

        if alert_type == "anomaly":
            # Check for anomalies in the data
            anomalies = analytics_data.get("anomalies", [])
            for anomaly in anomalies:
                if anomaly.get("type") == alert["id"].replace("_alert", "").replace(
                    "_", "_"
                ):
                    change_percent = abs(anomaly.get("value", 0))
                    if change_percent >= threshold:
                        return True

        elif alert_type == "threshold":
            if alert["id"] == "error_rate_high":
                error_rate = analytics_data.get("error_rate", 0)
                return error_rate >= threshold
            elif alert["id"] == "user_engagement_low":
                active_users = analytics_data.get("active_users", 0)
                return active_users < threshold

        elif alert_type == "trend":
            # Check for trend changes
            trends = analytics_data.get("trends", {})
            for metric, trend_data in trends.items():
                change_percent = trend_data.get("change_percent", 0)
                if alert["id"] == "conversion_drop" and metric == "conversions":
                    if change_percent <= threshold:  # threshold is negative
                        return True

        return False

    def _get_alert_severity(self, alert):
        """Determine alert severity"""
        alert_id = alert.get("id", "")

        if "error" in alert_id or "critical" in alert_id:
            return "critical"
        elif "spike" in alert_id or "drop" in alert_id:
            return "high"
        else:
            return "medium"

    def update_alert(self, alert_id, updates):
        """Update alert configuration"""
        for alert in self.alerts:
            if alert["id"] == alert_id:
                alert.update(updates)
                return True
        return False

    def get_alerts(self):
        """Get all alerts configuration"""
        return self.alerts


# Global alert system instance
alert_system = AlertSystem()


@analytics_bp.route("/api/alerts/config", methods=["GET"])
@require_admin_login
async def get_alerts_config():
    """Get alerts configuration"""
    try:
        return jsonify(
            {
                "alerts": alert_system.get_alerts(),
                "total_alerts": len(alert_system.get_alerts()),
                "generated_at": datetime.now().isoformat(),
            }
        )
    except Exception as e:
        print(f"Error getting alerts config: {type(e).__name__}")
        return jsonify({"error": "Failed to get alerts configuration"})


@analytics_bp.route("/api/alerts/config/<alert_id>", methods=["PUT"])
@require_admin_login
async def update_alert_config(alert_id):
    """Update alert configuration"""
    try:
        data = request.get_json()
        success = alert_system.update_alert(alert_id, data)

        if success:
            return jsonify({"success": True, "message": "Alert updated successfully"})
        else:
            return jsonify({"error": "Alert not found"}), 404

    except Exception as e:
        print(f"Error updating alert config: {type(e).__name__}")
        return jsonify({"error": "Failed to update alert configuration"})


@analytics_bp.route("/api/alerts/check", methods=["GET"])
@require_admin_login
async def check_alerts():
    """Check for triggered alerts"""
    try:
        import asyncio

        from advanced_analytics import AdvancedAnalytics

        analytics = AdvancedAnalytics()

        # Get current analytics data
        loop = asyncio.get_event_loop()
        anomalies = await loop.run_in_executor(None, analytics.detect_anomalies, 1)
        insights = await loop.run_in_executor(None, analytics.generate_insights_report)

        analytics_data = {
            "anomalies": anomalies,
            "error_rate": 0,  # Would need to calculate from actual error events
            "active_users": insights.get("user_segments", {})
            .get("regular_users", {})
            .get("count", 0),
            "trends": insights.get("kpi_trends", {}).get("trends", {}),
        }

        triggered_alerts = alert_system.check_alerts(analytics_data)

        return jsonify(
            {
                "triggered_alerts": triggered_alerts,
                "total_triggered": len(triggered_alerts),
                "checked_at": datetime.now().isoformat(),
            }
        )

    except Exception as e:
        print(f"Error checking alerts: {type(e).__name__}")
        return jsonify({"error": "Failed to check alerts"})


@analytics_bp.route("/api/alerts/history", methods=["GET"])
@require_admin_login
async def get_alerts_history():
    """Get alerts history (placeholder for now)"""
    try:
        history = []
        if current_app and current_app.config.get("DEMO_MODE"):
            history = [
                {
                    "id": "alert_001",
                    "alert_id": "traffic_spike",
                    "name": "Traffic Spike Alert",
                    "severity": "high",
                    "triggered_at": (datetime.now() - timedelta(hours=2)).isoformat(),
                    "resolved_at": (datetime.now() - timedelta(hours=1)).isoformat(),
                    "status": "resolved",
                },
                {
                    "id": "alert_002",
                    "alert_id": "error_rate_high",
                    "name": "High Error Rate Alert",
                    "severity": "critical",
                    "triggered_at": (datetime.now() - timedelta(minutes=30)).isoformat(),
                    "resolved_at": None,
                    "status": "active",
                },
            ]

        return jsonify(
            {
                "history": history,
                "total_alerts": len(history),
                "active_alerts": len([h for h in history if h["status"] == "active"]),
                "generated_at": datetime.now().isoformat(),
                "available": bool(history),
                "reason": "" if history else "No persisted alert history table is available.",
                "confidence": "low" if not history else "demo",
                "source_tables": ["alert_history"],
            }
        )

    except Exception as e:
        print(f"Error getting alerts history: {type(e).__name__}")
        return jsonify({"error": "Failed to get alerts history"})


@analytics_bp.route("/api/analytics/export")
@require_admin_login
async def export_analytics():
    """Export analytics data in various formats"""
    try:
        import asyncio

        format_type = request.args.get("format", "json")
        export_type = request.args.get("type", "dashboard")

        # Get analytics data
        try:
            from analytics_service import analytics_service

            # Run analytics_service in thread executor
            loop = asyncio.get_event_loop()
            data = await loop.run_in_executor(
                None, analytics_service.get_dashboard_analytics
            )
        except ImportError:
            from admin_analytics import AnalyticsEngine

            # Run in thread executor
            loop = asyncio.get_event_loop()
            data = await loop.run_in_executor(None, AnalyticsEngine.get_dashboard_stats)

        # Add export metadata
        data["export_info"] = {
            "generated_at": datetime.now().isoformat(),
            "format": format_type,
            "type": export_type,
            "version": "1.0",
        }

        if format_type == "json":
            response = jsonify(data)
            response.headers["Content-Disposition"] = (
                f"attachment; filename=helpchain_analytics_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
            )
            return response

        elif format_type == "csv":
            # Convert to CSV format
            import csv
            import io

            output = io.StringIO()
            writer = csv.writer(output)

            # Write headers
            writer.writerow(["Metric", "Value", "Category"])

            # Write data
            for category, values in data.items():
                if isinstance(values, dict):
                    for key, value in values.items():
                        writer.writerow([key, str(value), category])
                else:
                    writer.writerow([category, str(values), "general"])

            csv_data = output.getvalue()
            output.close()

            response = Response(
                csv_data,
                mimetype="text/csv",
                headers={
                    "Content-Disposition": f"attachment; filename=helpchain_analytics_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
                },
            )
            return response

        else:
            return jsonify({"error": "Unsupported format"}), 400

    except Exception as e:
        print(f"Error exporting analytics: {type(e).__name__}: {e}")
        return jsonify({"error": "Export failed"}), 500



