#!/usr/bin/env python
"""
Test script for admin analytics functionality
"""

from flask import render_template


def test_admin_analytics(authenticated_admin_client):
    """Test admin analytics page access"""
    # Access analytics page
    response = authenticated_admin_client.get("/admin_analytics")

    assert response.status_code == 200, f"Expected 200, got {response.status_code}"

    # Check if charts are present
    content = response.get_data(as_text=True)
    assert "chart-container" in content, "Chart containers not found in HTML"
    assert "Chart.js" in content, "Chart.js library not found"
    assert "trendsData" in content, "Trends data script not found"


def test_admin_analytics_empty_state_uses_real_data_only(app):
    """Empty analytics dashboards must not render fabricated production metrics."""
    with app.test_request_context("/analytics/admin_analytics"):
        content = render_template(
            "admin_analytics_professional.html",
            dashboard_stats={
                "overview": {
                    "total_page_views": 0,
                    "conversion_rate": 0,
                    "avg_session_time": 0,
                    "unique_visitors": 0,
                    "bounce_rate": 0,
                }
            },
            performance_metrics={
                "completed_requests": 0,
                "active_requests": 0,
                "active_volunteers": 0,
                "utilization_rate": 0,
            },
            anomalies=[],
            predictions={"labels": [], "requests_predicted": [], "volunteers_predicted": []},
            recommendations=[],
            trends_data={"labels": [], "requests": [], "completed": [], "volunteers": []},
            category_stats={"categories": [], "counts": []},
            geo_data={"requests": [], "volunteers": []},
            live_stats={},
            advanced_analytics={},
            filter_summary="Last 30 days",
            filters_context={"days": 30, "start_date": None, "end_date": None},
        )

    assert "No operational analytics available yet." in content
    assert "Live data only" in content
    assert "No trend data available yet." in content
    assert "No category data available yet." in content
    assert "No geolocation data available yet." in content
    assert "No prediction data available yet." in content
    assert 'id="geoData"' in content
    assert '+12.5%' not in content
    assert '+2.1%' not in content
    assert '-0.5' not in content
    assert '+8.3' not in content


def test_analytics_stream_has_no_sample_events(authenticated_admin_client):
    """Production analytics stream must not fall back to fabricated events."""
    response = authenticated_admin_client.get("/analytics/stream")

    assert response.status_code == 200

    payload = response.get_json()
    assert payload["sse_enabled"] is False
    assert payload["events"] == []
    assert payload["status"] == "idle"
    assert payload["message"] == "No analytics events available yet."
