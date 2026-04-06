import os
from flask_dotenv import DotEnv
name = os.getenv('secret_key')
print(name)