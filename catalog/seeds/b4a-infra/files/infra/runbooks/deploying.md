# Deploying

1. Change the right layer (see infra/README.md); never edit archive/ or backups.
2. Open a pull request; CI renders every service's environment and diffs it.
3. Merge; the deployer rolls out region by region.
