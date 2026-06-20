from setuptools import setup, find_packages


setup(
    name="handball",
    author="Oliver Hvidsten",
    version="0.1",
    packages=find_packages(),
    install_requires=[
        "numpy>=1.26",          # used by handball.players / domain (core import path)
        "sqlalchemy>=2",
        "psycopg[binary]>=3",
        "alembic>=1.13",
    ],
    extras_require={
        # The write API (api/). Core sim + data layer do not need these.
        "api": [
            "fastapi>=0.110",
            "uvicorn[standard]>=0.29",
            # [crypto] pulls in `cryptography`, required to verify Supabase's
            # ES256 (asymmetric) JWTs. Without it every authed request 401s.
            "pyjwt[crypto]>=2.8",
        ],
    },
)