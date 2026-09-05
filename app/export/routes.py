"""Excel export route: build and download a filtered feedback workbook."""

from __future__ import annotations

from datetime import datetime
from urllib.parse import quote

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.export import service
from app.feedback.service import HistoryFilters

router = APIRouter(tags=["export"])


@router.get("/export.xlsx")
def export_workbook(request: Request, db: Session = Depends(get_db)):
    q = request.query_params
    filters = HistoryFilters(
        date_from=q.get("date_from") or None,
        date_to=q.get("date_to") or None,
        session_type=q.get("session_type") or None,
        class_id=q.get("class_id") or None,
        student_id=q.get("student_id") or None,
        status=q.get("status") or None,
    )
    content = service.build_workbook(db, filters)
    filename = quote(f"教学反馈_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx")
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"},
    )
