from setuptools import setup, find_packages

with open('requirements.txt') as f:
	install_requires = f.read().strip().split('\n')

# get version from __version__ variable in network_billing_system/__init__.py
from network_billing_system import __version__ as version

setup(
	name='network_billing_system',
	version=version,
	description='A complete billing system integrated with pfsense',
	author='stephen',
	author_email='wachangasteve@gmail.com',
	packages=find_packages(),
	zip_safe=False,
	include_package_data=True,
	install_requires=install_requires
)
