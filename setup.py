#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Setup script for IskBot
Telegram bot for automatic generation of court claims
"""

from setuptools import find_packages, setup


# Читаем README для описания
def read_readme():
    """Читает файл README.md для описания проекта."""
    with open('README.md', 'r', encoding='utf-8') as f:
        return f.read()


# Читаем requirements
def read_requirements():
    """Читает файл requirements.txt для зависимостей."""
    with open('requirements.txt', 'r', encoding='utf-8') as f:
        return [
            line.strip() for line in f
            if line.strip() and not line.startswith('#')
        ]


setup(
    name='iskbot',
    version='2.0.0',
    description='Telegram bot for automatic generation of court claims',
    long_description=read_readme(),
    long_description_content_type='text/markdown',
    author='IskBot Team',
    author_email='support@iskbot.com',
    url='https://github.com/your-username/IskBot',
    packages=find_packages(),
    install_requires=read_requirements(),
    python_requires='>=3.8',
    classifiers=[
        'Development Status :: 4 - Beta',
        'Intended Audience :: Legal Industry',
        'License :: OSI Approved :: MIT License',
        'Programming Language :: Python :: 3',
        'Programming Language :: Python :: 3.8',
        'Programming Language :: Python :: 3.9',
        'Programming Language :: Python :: 3.10',
        'Programming Language :: Python :: 3.11',
        'Topic :: Communications :: Chat',
        'Topic :: Office/Business',
        'Topic :: Text Processing :: Markup',
    ],
    keywords='telegram bot court claim legal document generation',
    entry_points={
        'console_scripts': [
            'iskbot=main:main',
        ],
    },
    include_package_data=True,
    zip_safe=False,
)
