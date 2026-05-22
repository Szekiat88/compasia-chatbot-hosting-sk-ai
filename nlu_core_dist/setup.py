"""
Build script for nlu-core.
Forces a platform-specific wheel so the compiled extension is included correctly.
"""
from setuptools import setup, find_packages
from setuptools.dist import Distribution


class BinaryDistribution(Distribution):
    """Declare this as a non-pure distribution so setuptools generates a platform wheel."""

    def has_ext_modules(self):
        return True


setup(
    name="nlu-core",
    version="1.0.0",
    description="Multilingual NLU engine for intent classification, escalation detection, and conversation management.",
    long_description=open("README.md").read(),
    long_description_content_type="text/markdown",
    author="MS88",
    license="MIT",
    python_requires=">=3.10",
    packages=find_packages(),
    package_data={"nlu_core": ["*.so"]},
    distclass=BinaryDistribution,
    classifiers=[
        "Programming Language :: Python :: 3",
        "Operating System :: MacOS",
        "Topic :: Scientific/Engineering :: Artificial Intelligence",
        "Topic :: Software Development :: Libraries :: Python Modules",
        "Intended Audience :: Developers",
    ],
    keywords=["nlp", "nlu", "intent-classification", "chatbot", "multilingual", "malay", "english"],
)
