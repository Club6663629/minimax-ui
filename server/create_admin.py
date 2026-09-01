"""手动创建管理员账号。

用法：python create_admin.py <email> <username> <password>
（也可用环境变量 ADMIN_EMAIL/ADMIN_PASSWORD 在启动时自动创建）
"""
import sys

from app.auth import hash_password
from app.database import Base, SessionLocal, engine
from app.models import User


def main() -> None:
    if len(sys.argv) != 4:
        print("用法: python create_admin.py <email> <username> <password>")
        sys.exit(1)
    email, username, password = sys.argv[1], sys.argv[2], sys.argv[3]

    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        if db.query(User).filter(User.email == email.lower()).first():
            print(f"邮箱已存在: {email}")
            sys.exit(1)
        db.add(
            User(
                email=email.lower(),
                username=username,
                password_hash=hash_password(password),
                role="admin",
            )
        )
        db.commit()
        print(f"管理员已创建: {email}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
