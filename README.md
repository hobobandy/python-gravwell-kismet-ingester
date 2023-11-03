# python-gravwell-kismet-ingester

python script to transfer kismet data to gravwell for ingest

## how to run

1. Create a user configuration file (see config/example.toml)
2. Run the package as a module:

```bash
poetry run python -B -m python-gravwell-kismet-ingester -c config/user.toml
```

## warning

This is in heavy development, not meant for use yet.
