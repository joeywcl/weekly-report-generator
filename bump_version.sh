#!/bin/bash
# Bump version - Usage: ./bump_version.sh [major|minor|patch]

set -e

VERSION_FILE="VERSION"
BUMP_TYPE=${1:-patch}

if [ ! -f "$VERSION_FILE" ]; then
    echo "1.0.0" > "$VERSION_FILE"
    echo "Created VERSION file: 1.0.0"
    exit 0
fi

CURRENT=$(cat "$VERSION_FILE")
IFS='.' read -r -a parts <<< "$CURRENT"

MAJOR="${parts[0]}"
MINOR="${parts[1]}"
PATCH="${parts[2]}"

case "$BUMP_TYPE" in
    major)
        MAJOR=$((MAJOR + 1))
        MINOR=0
        PATCH=0
        ;;
    minor)
        MINOR=$((MINOR + 1))
        PATCH=0
        ;;
    patch)
        PATCH=$((PATCH + 1))
        ;;
    *)
        echo "Usage: $0 [major|minor|patch]"
        exit 1
        ;;
esac

NEW_VERSION="$MAJOR.$MINOR.$PATCH"
echo "$NEW_VERSION" > "$VERSION_FILE"

echo "✓ Bumped version: $CURRENT → $NEW_VERSION"
echo ""
echo "Next steps:"
echo "  git add VERSION"
echo "  git commit -m \"chore: bump version to $NEW_VERSION\""
echo "  git tag v$NEW_VERSION"
echo "  git push origin main --tags"
