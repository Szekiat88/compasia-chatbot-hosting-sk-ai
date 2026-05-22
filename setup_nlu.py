from setuptools import setup, find_packages
from Cython.Build import cythonize

setup(
    name="nlu-engine-core",
    version="1.0.0",
    description="Multilingual NLU engine for intent classification",
    ext_modules=cythonize(
        ["engine_core.py", "ml_intent_engine.py", "_ai_config.py"],
        compiler_directives={"language_level": "3"},
    ),
    zip_safe=False,
)
