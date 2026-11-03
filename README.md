# Учебная платформа онлайн-аукционов (Django + SQLite)

    pip install -r requirements.txt
    python manage.py makemigrations auctions
    python manage.py migrate
    python manage.py createsuperuser
    python manage.py runserver
    python manage.py test

Победитель определяется автоматически при открытии списка/лота после окончания времени,
либо командой `python manage.py close_auctions` (можно повесить на cron).
