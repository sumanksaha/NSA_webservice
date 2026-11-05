import sys, os

sys.path.insert(0, ".")
from alembic.config import Config
from alembic.script import ScriptDirectory

# Read the alembic ini manually
config = Config()
import configparser

c = configparser.ConfigParser()
c.read("migrations/alembic.ini")
head = c.get("alembic", "head", fallback=None)
print("HEAD from ini:", head)

# Set script_location properly
config.set_main_option("script_location", "migrations/alembic.ini")

script_dir = ScriptDirectory.from_config(config)
print("Versions count:", len(list(script_dir.versions)))

# Print all versions
for v in script_dir.versions:
    print("  ", v)
