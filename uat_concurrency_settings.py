from config.settings import *
import tempfile
DATABASES['default']['TEST']={'NAME':str(Path(tempfile.gettempdir())/f'partsdesk-uat-concurrency-{os.getpid()}.sqlite3')}
