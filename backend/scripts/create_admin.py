import getpass
import sys

from sqlalchemy import select

sys.path.insert(0, "backend")

from app.db import SessionLocal, init_db  # noqa: E402
from app.models import AdminUser  # noqa: E402
from app.security import hash_password  # noqa: E402


def main() -> None:
    init_db()
    username = input("管理员账号 [admin]: ").strip() or "admin"
    password = getpass.getpass("管理员密码（至少 15 位）: ")
    if len(password) < 15:
        raise SystemExit("密码长度至少 15 位")
    password_again = getpass.getpass("再次输入密码: ")
    if password != password_again:
        raise SystemExit("两次密码不一致")
    with SessionLocal() as db:
        user = db.scalar(select(AdminUser).where(AdminUser.username == username))
        if user:
            user.password_hash = hash_password(password)
            user.is_active = True
        else:
            db.add(AdminUser(username=username, password_hash=hash_password(password)))
        db.commit()
    print(f"管理员 {username} 已就绪")


if __name__ == "__main__":
    main()
