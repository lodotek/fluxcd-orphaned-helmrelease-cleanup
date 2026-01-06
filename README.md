# fluxcd-orphaned-helmrelease-cleanup
Python script to detect and remove orphaned Flux helmReleases

__Features:__

- Fetches all Kustomizations and extracts HelmReleases from their inventories

- Identifies HelmReleases with `kustomize.toolkit.fluxcd.io/name` and `kustomize.toolkit.fluxcd.io/namespace` labels that are NOT in any Kustomization's inventory

- Supports namespace filtering (`-n/--namespace`)

- Multiple output formats: table (default), json, yaml (`-o/--output`)

- Interactive cleanup mode (`--cleanup`) that prompts per-namespace with options:

  - `y` - delete all in namespace
  - `n` - skip namespace
  - `s` - select individual releases
  - `q` - quit

__Usage examples:__

```bash
# List all orphaned HelmReleases (table format)
python scripts/helm-gitops-drift-detector/find-orphaned-helmreleases.py

# Filter by namespace
python scripts/helm-gitops-drift-detector/find-orphaned-helmreleases.py -n dev01-shield

# Output as JSON
python scripts/helm-gitops-drift-detector/find-orphaned-helmreleases.py -o json

# Interactive cleanup
python scripts/helm-gitops-drift-detector/find-orphaned-helmreleases.py
```
