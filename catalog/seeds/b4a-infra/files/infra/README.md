# Deployment config

The deployer builds each service's environment from these layers, in this order; a later layer overrides an
earlier one:

1. `defaults/service.env`
2. `regions/<region>/region.env`
3. `regions/<region>/<environment>/env.env`
4. `regions/<region>/<environment>/services/<service>.env`

`<environment>` is one of `prod`, `staging` or `dev`. Within a file the last assignment of a key wins. Lines that
start with `#` are comments. The deployer reads only files ending in `.env` at those paths: editor backups
(`*.bak`, `*.orig`), `archive/` and any other folder under a region (such as the decommissioned `prod-2025/`) are
never deployed.

Keys are `KEY=value` with no spaces around `=`. Boolean values are `true` or `false`.
