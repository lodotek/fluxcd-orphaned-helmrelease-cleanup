#!/usr/bin/env python3
"""
Find orphaned FluxCD HelmRelease resources.

Orphaned HelmReleases are resources that:
1. Were previously managed by a Flux Kustomization (indicated by kustomize labels)
2. Are no longer tracked in any Kustomization's inventory (because the Kustomization
   had prune: false (at some point) and the resource was removed from the source repo)

Usage:
    python find-orphaned-helmreleases.py [--namespace NAMESPACE] [--output FORMAT] [--cleanup]
"""

import argparse
import json
import subprocess
import sys
from collections import defaultdict
from typing import Dict, List, Optional, Set, Tuple


def run_kubectl(args: List[str]) -> Tuple[bool, str]:
    """Run kubectl command and return success status and output."""
    cmd = ["kubectl"] + args
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return True, result.stdout
    except subprocess.CalledProcessError as e:
        return False, e.stderr
    except FileNotFoundError:
        return False, "kubectl not found in PATH"


def get_all_kustomizations() -> List[dict]:
    """Fetch all Flux Kustomizations from the cluster."""
    success, output = run_kubectl([
        "get", "kustomizations.kustomize.toolkit.fluxcd.io",
        "-A", "-o", "json"
    ])
    if not success:
        print(f"Error fetching Kustomizations: {output}", file=sys.stderr)
        return []
    
    try:
        data = json.loads(output)
        return data.get("items", [])
    except json.JSONDecodeError as e:
        print(f"Error parsing Kustomizations JSON: {e}", file=sys.stderr)
        return []


def get_managed_helmreleases_from_inventory(kustomizations: List[dict]) -> Set[str]:
    """
    Extract all HelmReleases from Kustomization inventories.
    Returns a set of "namespace/name" strings for HelmReleases.
    """
    managed_releases = set()
    
    for ks in kustomizations:
        ks_name = ks.get("metadata", {}).get("name", "unknown")
        ks_namespace = ks.get("metadata", {}).get("namespace", "unknown")
        
        inventory = ks.get("status", {}).get("inventory", {})
        entries = inventory.get("entries", [])
        
        for entry in entries:
            # Entry format: {"id": "namespace_name_group_kind", "v": "version"}
            entry_id = entry.get("id", "")
            
            # Parse the entry ID - format is: namespace_name_group_kind
            # For HelmRelease: namespace_name_helm.toolkit.fluxcd.io_HelmRelease
            if "_HelmRelease" in entry_id and "helm.toolkit.fluxcd.io" in entry_id:
                # Extract namespace and name from the entry
                parts = entry_id.split("_")
                if len(parts) >= 2:
                    # First part is namespace, second is name
                    ns = parts[0]
                    name = parts[1]
                    managed_releases.add(f"{ns}/{name}")
    
    return managed_releases


def get_all_helmreleases(namespace: Optional[str] = None) -> List[dict]:
    """Fetch all HelmReleases from the cluster, optionally filtered by namespace."""
    args = ["get", "helmreleases.helm.toolkit.fluxcd.io", "-o", "json"]
    if namespace:
        args.extend(["-n", namespace])
    else:
        args.append("-A")
    
    success, output = run_kubectl(args)
    if not success:
        print(f"Error fetching HelmReleases: {output}", file=sys.stderr)
        return []
    
    try:
        data = json.loads(output)
        return data.get("items", [])
    except json.JSONDecodeError as e:
        print(f"Error parsing HelmReleases JSON: {e}", file=sys.stderr)
        return []


def find_orphaned_helmreleases(
    helmreleases: List[dict],
    managed_releases: Set[str]
) -> List[dict]:
    """
    Find HelmReleases that have kustomize labels but are not in any inventory.
    
    Returns list of orphaned HelmRelease objects with additional metadata.
    """
    orphans = []
    
    for hr in helmreleases:
        metadata = hr.get("metadata", {})
        name = metadata.get("name", "")
        namespace = metadata.get("namespace", "")
        labels = metadata.get("labels", {})
        
        # Check for kustomize management labels
        ks_name = labels.get("kustomize.toolkit.fluxcd.io/name")
        ks_namespace = labels.get("kustomize.toolkit.fluxcd.io/namespace")
        
        # If it has kustomize labels, it was managed by a Kustomization
        if ks_name and ks_namespace:
            release_key = f"{namespace}/{name}"
            
            # If not in any inventory, it's orphaned
            if release_key not in managed_releases:
                orphans.append({
                    "name": name,
                    "namespace": namespace,
                    "original_kustomization_name": ks_name,
                    "original_kustomization_namespace": ks_namespace,
                    "labels": labels,
                    "raw": hr
                })
    
    return orphans


def print_orphans_table(orphans: List[dict]) -> None:
    """Print orphaned HelmReleases in a table format."""
    if not orphans:
        print("No orphaned HelmReleases found.")
        return
    
    # Header
    print(f"\n{'NAMESPACE':<30} {'NAME':<40} {'ORIGINAL KUSTOMIZATION':<50}")
    print("-" * 120)
    
    for orphan in orphans:
        ks_ref = f"{orphan['original_kustomization_namespace']}/{orphan['original_kustomization_name']}"
        print(f"{orphan['namespace']:<30} {orphan['name']:<40} {ks_ref:<50}")
    
    print(f"\nTotal orphaned HelmReleases: {len(orphans)}")


def print_orphans_json(orphans: List[dict]) -> None:
    """Print orphaned HelmReleases in JSON format."""
    output = []
    for orphan in orphans:
        output.append({
            "name": orphan["name"],
            "namespace": orphan["namespace"],
            "originalKustomization": {
                "name": orphan["original_kustomization_name"],
                "namespace": orphan["original_kustomization_namespace"]
            }
        })
    print(json.dumps(output, indent=2))


def print_orphans_yaml(orphans: List[dict]) -> None:
    """Print orphaned HelmReleases in YAML format."""
    for orphan in orphans:
        print(f"- name: {orphan['name']}")
        print(f"  namespace: {orphan['namespace']}")
        print(f"  originalKustomization:")
        print(f"    name: {orphan['original_kustomization_name']}")
        print(f"    namespace: {orphan['original_kustomization_namespace']}")


def delete_helmrelease(namespace: str, name: str) -> bool:
    """Delete a HelmRelease."""
    success, output = run_kubectl([
        "delete", "helmrelease.helm.toolkit.fluxcd.io",
        "-n", namespace, name
    ])
    if success:
        print(f"  ✓ Deleted HelmRelease {namespace}/{name}")
    else:
        print(f"  ✗ Failed to delete HelmRelease {namespace}/{name}: {output}")
    return success


def cleanup_orphans_interactive(orphans: List[dict]) -> None:
    """Interactively prompt user to cleanup orphans, one namespace at a time."""
    if not orphans:
        print("No orphaned HelmReleases to clean up.")
        return
    
    # Group orphans by namespace
    by_namespace: Dict[str, List[dict]] = defaultdict(list)
    for orphan in orphans:
        by_namespace[orphan["namespace"]].append(orphan)
    
    namespaces = sorted(by_namespace.keys())
    
    print(f"\n{'='*60}")
    print("ORPHANED HELMRELEASE CLEANUP")
    print(f"{'='*60}")
    print(f"\nFound {len(orphans)} orphaned HelmReleases in {len(namespaces)} namespaces.")
    print("\nYou will be prompted for each namespace.")
    print("Options: [y]es delete all in namespace, [n]o skip namespace, [s]elect individual, [q]uit\n")
    
    deleted_count = 0
    skipped_count = 0
    
    for ns in namespaces:
        ns_orphans = by_namespace[ns]
        print(f"\n{'─'*60}")
        print(f"Namespace: {ns}")
        print(f"Orphaned HelmReleases ({len(ns_orphans)}):")
        for orphan in ns_orphans:
            ks_ref = f"{orphan['original_kustomization_namespace']}/{orphan['original_kustomization_name']}"
            print(f"  - {orphan['name']} (was managed by: {ks_ref})")
        
        while True:
            choice = input(f"\nDelete all {len(ns_orphans)} HelmReleases in '{ns}'? [y/n/s/q]: ").lower().strip()
            
            if choice == 'q':
                print("\nCleanup aborted by user.")
                print(f"Summary: {deleted_count} deleted, {skipped_count + sum(len(by_namespace[n]) for n in namespaces[namespaces.index(ns):])} skipped")
                return
            
            elif choice == 'y':
                print(f"\nDeleting all HelmReleases in namespace '{ns}'...")
                for orphan in ns_orphans:
                    if delete_helmrelease(orphan["namespace"], orphan["name"]):
                        deleted_count += 1
                    else:
                        skipped_count += 1
                break
            
            elif choice == 'n':
                print(f"Skipping namespace '{ns}'")
                skipped_count += len(ns_orphans)
                break
            
            elif choice == 's':
                # Individual selection mode
                for orphan in ns_orphans:
                    ks_ref = f"{orphan['original_kustomization_namespace']}/{orphan['original_kustomization_name']}"
                    while True:
                        individual_choice = input(
                            f"  Delete '{orphan['name']}' (was managed by: {ks_ref})? [y/n/q]: "
                        ).lower().strip()
                        
                        if individual_choice == 'q':
                            print("\nCleanup aborted by user.")
                            return
                        elif individual_choice == 'y':
                            if delete_helmrelease(orphan["namespace"], orphan["name"]):
                                deleted_count += 1
                            else:
                                skipped_count += 1
                            break
                        elif individual_choice == 'n':
                            print(f"  Skipped {orphan['name']}")
                            skipped_count += 1
                            break
                        else:
                            print("  Invalid choice. Please enter y, n, or q.")
                break
            
            else:
                print("Invalid choice. Please enter y, n, s, or q.")
    
    print(f"\n{'='*60}")
    print("CLEANUP COMPLETE")
    print(f"{'='*60}")
    print(f"Deleted: {deleted_count}")
    print(f"Skipped: {skipped_count}")


def main():
    parser = argparse.ArgumentParser(
        description="Find orphaned FluxCD HelmRelease resources"
    )
    parser.add_argument(
        "-n", "--namespace",
        help="Filter HelmReleases by namespace (default: all namespaces)"
    )
    parser.add_argument(
        "-o", "--output",
        choices=["table", "json", "yaml"],
        default="table",
        help="Output format (default: table)"
    )
    parser.add_argument(
        "--cleanup",
        action="store_true",
        help="Interactively prompt to delete orphaned HelmReleases"
    )
    
    args = parser.parse_args()
    
    print("Fetching Kustomizations...")
    kustomizations = get_all_kustomizations()
    if not kustomizations:
        print("Warning: No Kustomizations found or unable to fetch them.", file=sys.stderr)
    
    print(f"Found {len(kustomizations)} Kustomizations")
    
    print("Building inventory of managed HelmReleases...")
    managed_releases = get_managed_helmreleases_from_inventory(kustomizations)
    print(f"Found {len(managed_releases)} HelmReleases in Kustomization inventories")
    
    print("Fetching HelmReleases from cluster...")
    helmreleases = get_all_helmreleases(args.namespace)
    print(f"Found {len(helmreleases)} HelmReleases in cluster")
    
    print("Analyzing for orphaned HelmReleases...")
    orphans = find_orphaned_helmreleases(helmreleases, managed_releases)
    
    # Output results
    if args.output == "table":
        print_orphans_table(orphans)
    elif args.output == "json":
        print_orphans_json(orphans)
    elif args.output == "yaml":
        print_orphans_yaml(orphans)
    
    # Interactive cleanup if requested
    if args.cleanup and orphans:
        cleanup_orphans_interactive(orphans)
    elif args.cleanup and not orphans:
        print("\nNo orphaned HelmReleases to clean up.")


if __name__ == "__main__":
    main()
