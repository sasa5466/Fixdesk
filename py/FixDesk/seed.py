import random
from datetime import datetime, timedelta
from database import (
    init_db, SessionLocal, User, Location, Category, Ticket,
    TicketHistory, UserRole, Priority, TicketStatus
)

def seed_data():
    init_db()
    db = SessionLocal()

    # Очистка
    db.query(TicketHistory).delete()
    db.query(Ticket).delete()
    db.query(User).delete()
    db.query(Location).delete()
    db.query(Category).delete()
    db.commit()

    # 1. Роли и Пользователи
    users = [
        User(email="admin@college.ru", password="123", full_name="Саня Админ", role=UserRole.admin),
        User(email="disp@college.ru", password="123", full_name="Слава Диспетчер", role=UserRole.dispatcher),
        User(email="exec1@college.ru", password="123", full_name="Улукбек Электрик", role=UserRole.executor),
        User(email="exec2@college.ru", password="123", full_name="А.Улукбек Сетевик", role=UserRole.executor),
        User(email="exec3@college.ru", password="123", full_name="Рифат Мебельщик", role=UserRole.executor),
        User(email="student1@college.ru", password="123", full_name="Анна Студентова", role=UserRole.applicant),
        User(email="student2@college.ru", password="123", full_name="Дмитрий Учеников", role=UserRole.applicant),
    ]
    db.add_all(users)
    db.commit()

    # 2. 6 Кабинетов
    locations = [
        Location(building="Корпус А", room="101"),
        Location(building="Корпус А", room="204"),
        Location(building="Корпус А", room="305"),
        Location(building="Корпус Б", room="12"),
        Location(building="Корпус Б", room="108"),
        Location(building="Корпус Б", room="210"),
    ]
    db.add_all(locations)

    # 3. 4 Категории
    categories = [
        Category(name="Электрика и освещение"),
        Category(name="Компьютеры и сеть"),
        Category(name="Проекторы и мультимедиа"),
        Category(name="Мебель и фурнитура"),
    ]
    db.add_all(categories)
    db.commit()

    executors = [u for u in users if u.role == UserRole.executor]
    applicants = [u for u in users if u.role == UserRole.applicant]

    # 4. 25 Заявок
    statuses = list(TicketStatus)
    priorities = list(Priority)

    for i in range(1, 26):
        author = random.choice(applicants)
        loc = random.choice(locations)
        cat = random.choice(categories)
        status_item = random.choice(statuses)
        priority_item = random.choice(priorities)

        created_at = datetime.utcnow() - timedelta(days=random.randint(1, 10), hours=random.randint(1, 12))
        resolved_at = None
        assignee_id = None

        if status_item in [TicketStatus.assigned, TicketStatus.in_progress, TicketStatus.resolved, TicketStatus.closed]:
            assignee_id = random.choice(executors).id

        if status_item in [TicketStatus.resolved, TicketStatus.closed]:
            resolved_at = created_at + timedelta(hours=random.randint(2, 48))

        ticket = Ticket(
            author_id=author.id,
            assignee_id=assignee_id,
            location_id=loc.id,
            category_id=cat.id,
            title=f"Заявка #{i}: Проблема в кабинете {loc.room}",
            description=f"Необходим ремонт или обслуживание по категории '{cat.name}'. Подробное описание проблемы номер {i}.",
            priority=priority_item,
            status=status_item,
            created_at=created_at,
            resolved_at=resolved_at,
            resolution_text="Работы выполнены в полном объеме." if resolved_at else None
        )
        db.add(ticket)
        db.commit()
        db.refresh(ticket)

        # История
        log = TicketHistory(
            ticket_id=ticket.id,
            actor_id=author.id,
            old_status="",
            new_status=TicketStatus.new.value,
            note="Создано автоматически через seed",
            created_at=created_at
        )
        db.add(log)
        db.commit()

    print(" Успешно сгенерированы демо-данные: 6 кабинетов, 4 категории, 3 исполнителя, 25 заявок!")
    db.close()

if __name__ == "__main__":
    seed_data()