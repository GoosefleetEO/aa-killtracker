import os

from setuptools import find_packages, setup

from killtracker import APP_NAME, HOMEPAGE_URL, __version__

# read the contents of your README file
this_directory = os.path.abspath(os.path.dirname(__file__))
with open(os.path.join(this_directory, "README.md"), encoding="utf-8") as f:
    long_description = f.read()

# allow setup.py to be run from any path
os.chdir(os.path.normpath(os.path.join(os.path.abspath(__file__), os.pardir)))

setup(
    name=APP_NAME,
    version=__version__,
    packages=find_packages(),
    include_package_data=True,
    license="MIT",
    description="An app for running killmail trackers with Alliance Auth and Discord",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url=HOMEPAGE_URL,
    author="Erik Kalkoken",
    author_email="kalkoken87@gmail.com",
    classifiers=[
        "Environment :: Web Environment",
        "Framework :: Django",
        "Framework :: Django :: 3.2",
        "Intended Audience :: End Users/Desktop",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
        "Programming Language :: Python",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Topic :: Internet :: WWW/HTTP",
        "Topic :: Internet :: WWW/HTTP :: Dynamic Content",
    ],
    python_requires="~=3.8",
    install_requires=[
        "allianceauth>=2.9",
        "dacite",
        "django-eveuniverse>=0.18",
        "allianceauth-app-utils>=1.14.2",
        "redis-simple-mq>=0.4",
        "dhooks-lite>=0.6",
    ],
)
