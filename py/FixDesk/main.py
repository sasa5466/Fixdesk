import os
import shutil
from typing import Optional, List
from datetime import datetime

from fastapi import FastAPI, Depends, HTTPException, Request, Form, UploadFile, File, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import or_

from database import (
    init_db, SessionLocal, User, Location, Category, Ticket,
    Attachment, Comment, TicketHistory, UserRole, Priority, TicketStatus
)

app = FastAPI(title="FixDesk")
os.makedirs("uploads", exist_ok=True)
app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")
templates = Jinja2Templates(directory="templates")

init_db()

# DB Dependency
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# Авторизация через куки (упрощенный вариант для семестрового проекта)
def get_current_user(request: Request, db: Session = Depends(get_db)) -> Optional[User]:
    user_id = request.cookies.get("user_id")
    if not user_id:
        return None
    return db.query(User).filter(User.id == int(user_id)).first()

def require_user(request: Request, db: Session = Depends(get_db)) -> User:
    user = get_current_user(request, db)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Требуется авторизация")
    return user

# Вспомогательная функция записи истории
def log_history(db: Session, ticket_id: int, actor_id: int, old_status: str, new_status: str, note: str = ""):
    history = TicketHistory(
        ticket_id=ticket_id,
        actor_id=actor_id,
        old_status=old_status,
        new_status=new_status,
        note=note
    )
    db.add(history)

# --- Маршруты Автoризации ---
@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request, db: Session = Depends(get_db)):
    users = db.query(User).all()
    return templates.TemplateResponse("login.html", {"request": request, "users": users})

@app.post("/login")
def login(user_id: int = Form(...)):
    response = RedirectResponse(url="/tickets", status_code=status.HTTP_303_SEE_OTHER)
    response.set_cookie(key="user_id", value=str(user_id))
    return response

@app.get("/logout")
def logout():
    response = RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie("user_id")
    return response

# --- Маршруты Заявок ---
@app.get("/", response_class=HTMLResponse)
def home():
    return RedirectResponse(url="/tickets")

@app.get("/tickets", response_class=HTMLResponse)
def get_tickets(
    request: Request,
    q: Optional[str] = None,
    category_id: Optional[int] = None,
    priority: Optional[str] = None,
    status_filter: Optional[str] = None,
    db: Session = Depends(get_db)
):
    user = get_current_user(request, db)
    if not user:
        return RedirectResponse(url="/login")

    query = db.query(Ticket).options(
        joinedload(Ticket.author),
        joinedload(Ticket.assignee),
        joinedload(Ticket.location),
        joinedload(Ticket.category)
    )

    # Ограничения видимости по ролям (RBAC)
    if user.role == UserRole.applicant:
        query = query.filter(Ticket.author_id == user.id)
    elif user.role == UserRole.executor:
        query = query.filter(Ticket.assignee_id == user.id)

    # Фильтры
    if q:
        query = query.filter(or_(Ticket.title.contains(q), Ticket.description.contains(q)))
    if category_id:
        query = query.filter(Ticket.category_id == category_id)
    if priority:
        query = query.filter(Ticket.priority == priority)
    if status_filter:
        query = query.filter(Ticket.status == status_filter)

    tickets = query.order_by(Ticket.created_at.desc()).all()
    categories = db.query(Category).all()
    executors = db.query(User).filter(User.role == UserRole.executor).all()

    return templates.TemplateResponse("tickets.html", {
        "request": request,
        "tickets": tickets,
        "user": user,
        "categories": categories,
        "executors": executors,
        "q": q or "",
        "selected_category": category_id,
        "selected_priority": priority or "",
        "selected_status": status_filter or ""
    })

@app.get("/tickets/create", response_class=HTMLResponse)
def create_ticket_page(request: Request, db: Session = Depends(get_db)):
    user = require_user(request, db)
    locations = db.query(Location).all()
    categories = db.query(Category).all()
    return templates.TemplateResponse("create_ticket.html", {
        "request": request, "user": user, "locations": locations, "categories": categories
    })

@app.post("/tickets")
async def create_ticket(
    request: Request,
    title: str = Form(...),
    description: str = Form(...),
    location_id: int = Form(...),
    category_id: int = Form(...),
    priority: str = Form("medium"),
    photos: List[UploadFile] = File([]),
    db: Session = Depends(get_db)
):
    user = require_user(request, db)
    
    # Ограничение до 3 фото
    valid_photos = [p for p in photos if p.filename]
    if len(valid_photos) > 3:
        raise HTTPException(status_code=400, detail="Можно прикрепить не более 3 фотографий")

    ticket = Ticket(
        author_id=user.id,
        location_id=location_id,
        category_id=category_id,
        title=title,
        description=description,
        priority=priority,
        status=TicketStatus.new
    )
    db.add(ticket)
    db.commit()
    db.refresh(ticket)

    # Загрузка фото
    for file in valid_photos:
        file_location = f"uploads/{ticket.id}_{file.filename}"
        with open(file_location, "wb+") as file_object:
            shutil.copyfileobj(file.file, file_object)
        attachment = Attachment(ticket_id=ticket.id, file_path=f"/{file_location}")
        db.add(attachment)

    log_history(db, ticket.id, user.id, "", TicketStatus.new.value, "Заявка создана")
    db.commit()

    return RedirectResponse(url=f"/tickets/{ticket.id}", status_code=status.HTTP_303_SEE_OTHER)

@app.get("/tickets/{ticket_id}", response_class=HTMLResponse)
def get_ticket_detail(ticket_id: int, request: Request, db: Session = Depends(get_db)):
    user = require_user(request, db)
    ticket = db.query(Ticket).filter(Ticket.id == ticket_id).first()
    if not ticket:
        raise HTTPException(status_code=404, detail="Заявка не найдена")

    # Проверка прав доступа к отдельной заявке
    if user.role == UserRole.applicant and ticket.author_id != user.id:
        raise HTTPException(status_code=403, detail="Доступ запрещен")
    if user.role == UserRole.executor and ticket.assignee_id != user.id:
        raise HTTPException(status_code=403, detail="Доступ запрещен")

    executors = db.query(User).filter(User.role == UserRole.executor).all()

    return templates.TemplateResponse("ticket_detail.html", {
        "request": request, "ticket": ticket, "user": user, "executors": executors
    })

# --- Действия по переходу статусов (FSM) ---

@app.post("/tickets/{ticket_id}/assign")
def assign_ticket(
    ticket_id: int,
    assignee_id: int = Form(...),
    priority: str = Form(...),
    request: Request = None,
    db: Session = Depends(get_db)
):
    user = require_user(request, db)
    if user.role not in [UserRole.dispatcher, UserRole.admin]:
        raise HTTPException(status_code=403, detail="Только диспетчер может назначать заявки")

    ticket = db.query(Ticket).get(ticket_id)
    old_status = ticket.status.value
    ticket.assignee_id = assignee_id
    ticket.priority = priority
    ticket.status = TicketStatus.assigned

    log_history(db, ticket.id, user.id, old_status, TicketStatus.assigned.value, f"Назначен исполнитель ID={assignee_id}")
    db.commit()
    return RedirectResponse(url=f"/tickets/{ticket_id}", status_code=status.HTTP_303_SEE_OTHER)

@app.post("/tickets/{ticket_id}/reject")
def reject_ticket(ticket_id: int, note: str = Form(...), request: Request = None, db: Session = Depends(get_db)):
    user = require_user(request, db)
    if user.role not in [UserRole.dispatcher, UserRole.admin]:
        raise HTTPException(status_code=403, detail="Только диспетчер может отклонить заявку")

    ticket = db.query(Ticket).get(ticket_id)
    old_status = ticket.status.value
    ticket.status = TicketStatus.rejected

    log_history(db, ticket.id, user.id, old_status, TicketStatus.rejected.value, f"Отклонено: {note}")
    db.commit()
    return RedirectResponse(url=f"/tickets/{ticket_id}", status_code=status.HTTP_303_SEE_OTHER)

@app.post("/tickets/{ticket_id}/start")
def start_ticket(ticket_id: int, request: Request, db: Session = Depends(get_db)):
    user = require_user(request, db)
    ticket = db.query(Ticket).get(ticket_id)

    if user.role != UserRole.executor or ticket.assignee_id != user.id:
        raise HTTPException(status_code=403, detail="Только назначенный исполнитель может взять заявку в работу")

    old_status = ticket.status.value
    ticket.status = TicketStatus.in_progress

    log_history(db, ticket.id, user.id, old_status, TicketStatus.in_progress.value, "Взято в работу")
    db.commit()
    return RedirectResponse(url=f"/tickets/{ticket_id}", status_code=status.HTTP_303_SEE_OTHER)

@app.post("/tickets/{ticket_id}/resolve")
def resolve_ticket(ticket_id: int, resolution_text: str = Form(...), request: Request = None, db: Session = Depends(get_db)):
    user = require_user(request, db)
    ticket = db.query(Ticket).get(ticket_id)

    if user.role != UserRole.executor or ticket.assignee_id != user.id:
        raise HTTPException(status_code=403, detail="Недостаточно прав")

    old_status = ticket.status.value
    ticket.status = TicketStatus.resolved
    ticket.resolution_text = resolution_text
    ticket.resolved_at = datetime.utcnow()

    log_history(db, ticket.id, user.id, old_status, TicketStatus.resolved.value, f"Решено: {resolution_text}")
    db.commit()
    return RedirectResponse(url=f"/tickets/{ticket_id}", status_code=status.HTTP_303_SEE_OTHER)

@app.post("/tickets/{ticket_id}/confirm")
def confirm_ticket(ticket_id: int, request: Request, db: Session = Depends(get_db)):
    user = require_user(request, db)
    ticket = db.query(Ticket).get(ticket_id)

    if ticket.author_id != user.id:
        raise HTTPException(status_code=403, detail="Только автор может закрыть заявку")

    old_status = ticket.status.value
    ticket.status = TicketStatus.closed
    ticket.closed_at = datetime.utcnow()

    log_history(db, ticket.id, user.id, old_status, TicketStatus.closed.value, "Решение подтверждено автором")
    db.commit()
    return RedirectResponse(url=f"/tickets/{ticket_id}", status_code=status.HTTP_303_SEE_OTHER)

@app.post("/tickets/{ticket_id}/reopen")
def reopen_ticket(ticket_id: int, note: str = Form(...), request: Request = None, db: Session = Depends(get_db)):
    user = require_user(request, db)
    ticket = db.query(Ticket).get(ticket_id)

    if ticket.author_id != user.id:
        raise HTTPException(status_code=403, detail="Только автор может вернуть заявку в работу")

    old_status = ticket.status.value
    ticket.status = TicketStatus.in_progress
    ticket.resolved_at = None # Согласно ТЗ: при возврате в работу resolved_at очищается

    log_history(db, ticket.id, user.id, old_status, TicketStatus.in_progress.value, f"Возврат в работу: {note}")
    db.commit()
    return RedirectResponse(url=f"/tickets/{ticket_id}", status_code=status.HTTP_303_SEE_OTHER)

@app.post("/tickets/{ticket_id}/comments")
def add_comment(ticket_id: int, text: str = Form(...), request: Request = None, db: Session = Depends(get_db)):
    user = require_user(request, db)
    comment = Comment(ticket_id=ticket_id, author_id=user.id, text=text)
    db.add(comment)
    db.commit()
    return RedirectResponse(url=f"/tickets/{ticket_id}", status_code=status.HTTP_303_SEE_OTHER)

# --- Отчет ---
@app.get("/report", response_class=HTMLResponse)
def get_report(request: Request, db: Session = Depends(get_db)):
    user = require_user(request, db)
    if user.role not in [UserRole.dispatcher, UserRole.admin]:
        raise HTTPException(status_code=403, detail="Доступ запрещен")

    total_tickets = db.query(Ticket).count()
    open_tickets = db.query(Ticket).filter(Ticket.status.in_([TicketStatus.new, TicketStatus.assigned, TicketStatus.in_progress])).count()
    resolved_tickets = db.query(Ticket).filter(Ticket.status.in_([TicketStatus.resolved, TicketStatus.closed]), Ticket.resolved_at.isnot(None)).all()

    # Безопасный расчет среднего времени без деления на ноль
    if not resolved_tickets:
        avg_time_hours = 0
    else:
        total_seconds = sum((t.resolved_at - t.created_at).total_seconds() for t in resolved_tickets)
        avg_time_hours = round((total_seconds / len(resolved_tickets)) / 3600, 2)

    return templates.TemplateResponse("report.html", {
        "request": request,
        "user": user,
        "total_tickets": total_tickets,
        "open_tickets": open_tickets,
        "resolved_count": len(resolved_tickets),
        "avg_time_hours": avg_time_hours
    })