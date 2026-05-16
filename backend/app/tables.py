from piccolo.columns import Timestamp, Varchar
from piccolo.columns.defaults.timestamp import TimestampNow
from piccolo.table import Table


class SystemHeartbeat(Table):
    created_at = Timestamp(default=TimestampNow())
    note = Varchar(length=200, default="")
