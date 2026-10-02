from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session
import math
import statistics

from app.db.session import get_db
from app.core.authorization import ensure_issue_access
from app.core.security import get_current_user

router = APIRouter()


class RiskScoreResponse(BaseModel):
    issue_id: int
    raw_risk_score: float
    risk_level: str
    included_causes: int
    included_effects: int


# -----------------------------
# Risk classification (NEW RULE)
# -----------------------------
def _classify_risk(normalized_score: float) -> str:
    if normalized_score >= 0.9:
        return "High"
    if normalized_score < -0.5:
        return "Low"
    return "Medium"


# -----------------------------
# Get issue pattern
# -----------------------------
def _get_issue_selected_pattern_id(db: Session, issue_id: int) -> int | None:
    row = db.execute(
        text("""
            SELECT selected_pattern_id
            FROM rpd_issues
            WHERE issue_id = :issue_id
        """),
        {"issue_id": issue_id},
    ).mappings().first()

    if not row:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="RpD issue not found.",
        )

    return row["selected_pattern_id"]


# -----------------------------
# Get pattern category
# -----------------------------
def _get_pattern_category(db: Session, pattern_id: int) -> str:
    row = db.execute(
        text("""
            SELECT category
            FROM patterns
            WHERE pattern_id = :pattern_id
        """),
        {"pattern_id": pattern_id},
    ).mappings().first()

    if not row:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="The selected pattern was not found.",
        )

    return row["category"]


# -----------------------------
# Get all patterns in same category
# -----------------------------
def _get_patterns_by_category(db: Session, category: str):
    rows = db.execute(
        text("""
            SELECT pattern_id
            FROM patterns
            WHERE category = :category
        """),
        {"category": category},
    ).mappings().all()

    return [r["pattern_id"] for r in rows]


# -----------------------------
# Existing function (unchanged)
# -----------------------------
def _get_pattern_based_rows(db: Session, pattern_id: int):
    result = db.execute(
        text("""
            SELECT
                pce.cause_id,
                c.p_c,
                pce.effect_id,
                cem.p_e_given_c,
                e.severity
            FROM pattern_cause_effects AS pce
            JOIN causes AS c
                ON c.cause_id = pce.cause_id
            JOIN effects AS e
                ON e.effect_id = pce.effect_id
            LEFT JOIN cause_effect_map AS cem
                ON cem.cause_id = pce.cause_id
               AND cem.effect_id = pce.effect_id
            WHERE pce.pattern_id = :pattern_id
            ORDER BY pce.cause_id, pce.effect_id
        """),
        {"pattern_id": pattern_id},
    )

    return result.mappings().all()


# -----------------------------
# Core risk calculation for one pattern
# -----------------------------
def _calculate_raw_risk(rows) -> float:
    rows = list(rows)
    if not rows:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Risk cannot be calculated because pattern risk data is missing.",
        )

    raw_risk_score = 0.0
    required_fields = ("cause_id", "effect_id", "p_c", "p_e_given_c", "severity")

    for row in rows:
        if any(field not in row or row[field] is None for field in required_fields):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Risk cannot be calculated because probability or severity "
                    "data is missing."
                ),
            )

        try:
            p_c = float(row["p_c"])
            p_e_given_c = float(row["p_e_given_c"])
            severity = float(row["severity"])
        except (TypeError, ValueError):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Risk cannot be calculated because probability or severity "
                    "data is invalid."
                ),
            )

        if (
            not all(math.isfinite(value) for value in (p_c, p_e_given_c, severity))
            or p_c < 0
            or p_e_given_c < 0
            or severity < 0
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Risk cannot be calculated because probability or severity "
                    "data is invalid."
                ),
            )

        contribution = p_c * p_e_given_c * severity
        if not math.isfinite(contribution):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Risk cannot be calculated because probability or severity "
                    "data is invalid."
                ),
            )

        raw_risk_score += contribution

    return raw_risk_score


# -----------------------------
# Final calculation
# -----------------------------
def _calculate_risk_from_rows(issue_id: int, rows, db: Session) -> RiskScoreResponse:

    # Step 1: raw risk for current issue
    raw_risk_score = _calculate_raw_risk(rows)

    # Step 2: get pattern baseline risks (CATEGORY BASED)
    selected_pattern_id = _get_issue_selected_pattern_id(db, issue_id)
    category = _get_pattern_category(db, selected_pattern_id)
    pattern_ids = _get_patterns_by_category(db, category)

    pattern_risks = []

    for pid in pattern_ids:
        p_rows = _get_pattern_based_rows(db, pid)
        risk = _calculate_raw_risk(p_rows)
        pattern_risks.append(risk)

    # Confirmed zero-risk baselines cannot be log-transformed and provide no
    # useful comparison point. Missing/invalid inputs have already raised 422.
    positive_pattern_risks = [risk for risk in pattern_risks if risk > 0]

    if raw_risk_score <= 0:
        robust_score = float("-inf")
    elif not positive_pattern_risks:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Risk level cannot be calculated because this pattern category "
                "has no positive baseline risk."
            ),
        )
    else:
        # Step 3: log transform
        log_risks = [math.log(risk) for risk in positive_pattern_risks]

        # Step 4: median + MAD
        median = statistics.median(log_risks)
        mad = statistics.median([abs(x - median) for x in log_risks])

        # Step 5: robust score
        log_risk = math.log(raw_risk_score)

        robust_score = 0.0 if mad == 0 else (log_risk - median) / mad

    return RiskScoreResponse(
        issue_id=issue_id,
        raw_risk_score=round(raw_risk_score, 4),
        risk_level=_classify_risk(robust_score),
        included_causes=len({r.get("cause_id") for r in rows if r.get("cause_id")}),
        included_effects=len(
            {
                (r.get("cause_id"), r.get("effect_id"))
                for r in rows
                if r.get("effect_id")
            }
        ),
    )


# -----------------------------
# API entry
# -----------------------------
@router.get("/issues/{issue_id}", response_model=RiskScoreResponse)
def calculate_issue_risk(
    issue_id: int,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    ensure_issue_access(db, issue_id, current_user)

    selected_pattern_id = _get_issue_selected_pattern_id(db, issue_id)

    if selected_pattern_id is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Select a pattern before calculating risk.",
        )

    rows = _get_pattern_based_rows(db, selected_pattern_id)

    return _calculate_risk_from_rows(issue_id, rows, db)
