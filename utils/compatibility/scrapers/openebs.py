import re
from collections import OrderedDict

import yaml
from packaging.version import InvalidVersion, Version

from utils import (
    current_kube_version,
    expand_kube_versions,
    fetch_page,
    print_error,
    update_compatibility_info,
)

APP_NAME = "openebs"
HELM_REPO_URL = "https://openebs.github.io/openebs"
CHART_NAME = "openebs"
MIN_APP_VERSION = Version("4.0.0")
CURRENT_DOC_URL = "https://openebs.io/docs/main/quickstart-guide/prerequisites"
VERSIONED_DOC_URL = (
    "https://openebs.io/docs/{major}.{minor}.x/quickstart-guide/installation"
)
REQUIREMENT_TEMPLATE = (
    "OpenEBS {major}.{minor}.x documentation requires Kubernetes {kube} or higher."
)
KUBE_REQUIREMENT_RE = re.compile(
    r"Kubernetes(?:\s+version)?\s+v?(\d+\.\d+)\s*(?:or\s+higher|\+)",
    re.IGNORECASE,
)


def _decode(content):
    return (
        content.decode("utf-8", errors="replace")
        if isinstance(content, bytes)
        else str(content)
    )


def parse_min_kube_version(content):
    if not content:
        return None
    match = KUBE_REQUIREMENT_RE.search(_decode(content))
    return match.group(1) if match else None


def parse_chart_index(content):
    if not content:
        return []

    try:
        index = yaml.safe_load(content)
    except yaml.YAMLError:
        print_error("Failed to parse OpenEBS Helm index.")
        return []

    entries = (index or {}).get("entries", {}).get(CHART_NAME, [])
    if not isinstance(entries, list):
        print_error("OpenEBS Helm index does not contain chart entries.")
        return []

    latest_by_minor = {}
    for entry in entries:
        app_version_raw = str(entry.get("appVersion", "")).lstrip("v")
        chart_version_raw = str(entry.get("version", "")).lstrip("v")
        try:
            app_version = Version(app_version_raw)
            chart_version = Version(chart_version_raw)
        except InvalidVersion:
            continue

        if (
            app_version < MIN_APP_VERSION
            or app_version.is_prerelease
            or app_version.is_devrelease
            or chart_version.is_prerelease
            or chart_version.is_devrelease
        ):
            continue

        key = (app_version.major, app_version.minor)
        current = latest_by_minor.get(key)
        if current is None or app_version > current["parsed_app"]:
            latest_by_minor[key] = {
                "parsed_app": app_version,
                "app_version": str(app_version),
                "chart_version": str(chart_version),
            }
        elif app_version == current["parsed_app"] and chart_version > Version(
            current["chart_version"]
        ):
            current["chart_version"] = str(chart_version)

    rows = [
        {
            "app_version": item["app_version"],
            "chart_version": item["chart_version"],
        }
        for item in latest_by_minor.values()
    ]
    return sorted(rows, key=lambda item: Version(item["app_version"]), reverse=True)


def versioned_doc_url(app_version):
    version = Version(app_version)
    return VERSIONED_DOC_URL.format(major=version.major, minor=version.minor)


def _minor_key(app_version):
    version = Version(app_version)
    return (version.major, version.minor)


def scrape():
    chart_index = fetch_page(f"{HELM_REPO_URL}/index.yaml")
    if not chart_index:
        return

    chart_releases = parse_chart_index(chart_index)
    if not chart_releases:
        print_error("No stable OpenEBS v4 chart releases found.")
        return

    current_kube = current_kube_version()
    if not current_kube:
        print_error("Could not determine Plural's current Kubernetes version.")
        return

    try:
        current_kube_version_parsed = Version(current_kube)
    except InvalidVersion:
        print_error(f"Invalid Kubernetes version: {current_kube}")
        return

    latest_minor = _minor_key(chart_releases[0]["app_version"])
    current_doc_min = None
    min_kube_by_minor = {}
    rows = []

    for release in chart_releases:
        app_version = release["app_version"]
        minor = _minor_key(app_version)

        if minor not in min_kube_by_minor:
            min_kube = parse_min_kube_version(fetch_page(versioned_doc_url(app_version)))
            if not min_kube and minor == latest_minor:
                if current_doc_min is None:
                    current_doc_min = parse_min_kube_version(fetch_page(CURRENT_DOC_URL))
                min_kube = current_doc_min
            min_kube_by_minor[minor] = min_kube

        min_kube = min_kube_by_minor[minor]
        if not min_kube:
            print_error(
                "Could not determine Kubernetes requirement for OpenEBS "
                f"{minor[0]}.{minor[1]}.x."
            )
            continue

        try:
            min_kube_parsed = Version(min_kube)
        except InvalidVersion:
            print_error(f"Invalid OpenEBS Kubernetes requirement: {min_kube}")
            continue

        if min_kube_parsed > current_kube_version_parsed:
            print_error(
                f"OpenEBS {app_version} requires Kubernetes {min_kube}, "
                f"newer than Plural's current {current_kube}."
            )
            continue

        kube_versions = expand_kube_versions(min_kube, current_kube)
        rows.append(
            OrderedDict(
                [
                    ("version", app_version),
                    ("kube", kube_versions),
                    (
                        "requirements",
                        [
                            REQUIREMENT_TEMPLATE.format(
                                major=minor[0], minor=minor[1], kube=min_kube
                            )
                        ],
                    ),
                    ("incompatibilities", []),
                    ("chart_version", release["chart_version"]),
                ]
            )
        )

    if not rows:
        print_error("No OpenEBS compatibility rows generated.")
        return

    update_compatibility_info(
        f"../../static/compatibilities/{APP_NAME}.yaml", rows
    )
