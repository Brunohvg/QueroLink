import dj_database_url

from .local import *

DATABASES = {
    'default': dj_database_url.config(
        default=config('DATABASE_URL'),
        conn_max_age=0,
        ssl_require=False,
    )
}
