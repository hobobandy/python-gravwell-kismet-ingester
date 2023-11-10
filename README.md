# python-gravwell-kismet-ingester

Python script to transfer Kismet data to Gravwell for ingest.

## Installation

1. Clone this repository to a folder.
2. Change to the folder, and depending on your Python package manager:
    * Pip: ```pip install -r requirements.txt```
    * Poetry: ```poetry install```
3. Create a user configuration file in the `config` folder. (see config/example.toml)
    * Kismet: Generate an API key through the web UI, ensure the API key has "read only" access (all it requires)
    * Gravwell: Generate a Token under "Tools & Resources", ensure the token has "Data Ingest" access (all it requires)

## Usage

1. Depending on your package manager:
   * Pip: ```python -B -m python-gravwell-kismet-ingester -c config/user.toml```
   * Poetry: ```poetry run python -B -m python-gravwell-kismet-ingester -c config/user.toml```

Note: The -B option is recommended during developmental testing to prevent issues with rapidly changing files vs cached files. (it is not a requirement though)

## Warning

This is in heavy development, not meant for production use yet. As mentioned in the configuration files, configuring too short of intervals may cause Kismet hangups, even with the lock mechanism implemented.
