from setuptools import find_packages, setup

packages = find_packages()
print(packages)

setup(
    name="pelicanfs",
    version="1.0.2",
    description="An FSSpec Implementation using the Pelican System",
    url="https://github.com/PelicanPlatform/pelicanfs",
    classifiers=[
        "Development Status :: 2 - Pre-Alpha",
        "Intended Audience :: Science/Research",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3 :: Only",
        "License :: OSI Approved :: Apache Software License",
    ],
    entry_points={
        "fsspec.specs": [
            "pelican=pelicanfs.core.PelicanFileSystem",
            "osdf=pelicanfs.core.OSDFFileSystem",
        ],
    },
    keywords="pelican, fsspec",
    packages=find_packages(
        where="src",
        include=["pelicanfs*"],
    ),
    long_description=open("README.md").read(),
    long_description_content_type="text/markdown",
    package_dir={"": "src"},
    python_requires=">=3.9, <4",
    install_requires=[
        "aiohttp>=3.9.4,<4",
        "cachetools>=5.3,<6",
        "fsspec>=2024.3.1",
        "aiowebdav>=0.1.0rc5",
    ],
    extras_require={
        "testing": ["pytest", "pytest-httpserver", "trustme"],
    },
    project_urls={
        "Source": "https://github.com/PelicanPlatform/pelicanfs",
        "Pelican Source": "https://github.com/PelicanPlatform/pelican",
        "Bug Reports": "https://github.com/PelicanPlatform/pelicanfs/issues",
    },
)
