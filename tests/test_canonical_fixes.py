#!/usr/bin/env python3
"""
Test the canonical name fixes.

Run this to verify:
1. Format mismatch fix (organizer checks identity scanner format)
2. Fuzzy matching (substring merging for Beethoven variants)
3. Comma-separated artist splitting
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from audio_meta.identity import IdentityScanner, IdentityCluster
from audio_meta.config import Settings


def test_substring_merging():
    """Test that Beethoven variants get merged."""
    print("\n=== Test 1: Substring Merging ===\n")

    # We don't actually need the scanner, just the helper methods
    # Use class methods directly
    class MockScanner:
        @staticmethod
        def _normalize_token(value: str) -> str:
            return IdentityScanner._normalize_token(value)

        @staticmethod
        def _build_clusters(raw_names, category):
            from audio_meta.identity import IdentityCluster
            clusters = {}
            for token, variants in raw_names.items():
                canonical = MockScanner._choose_canonical(variants)
                canonical_id = f"{category}::{token}"
                cluster = IdentityCluster(
                    canonical=canonical,
                    canonical_id=canonical_id,
                    variants=set(variants),
                    occurrences=len(variants),
                )
                clusters[token] = cluster
            return clusters

        @staticmethod
        def _choose_canonical(variants):
            return sorted(variants, key=lambda x: (x.count(',') > 0, not x[0].isupper(), len(x)))[0]

        @staticmethod
        def _merge_substring_clusters(clusters, category):
            # Import the actual method
            settings = Settings.load()
            scanner = IdentityScanner(settings.library, None)
            return scanner._merge_substring_clusters(clusters, category)

        @staticmethod
        def _split_names(value):
            settings = Settings.load()
            scanner = IdentityScanner(settings.library, None)
            return scanner._split_names(value)

    scanner = MockScanner()

    # Simulate raw data
    raw_composers = {
        "beethoven": ["Beethoven", "Beethoven"],
        "ludwigvonbeethoven": ["Ludwig von Beethoven", "Ludwig von Beethoven"],
    }

    # Build initial clusters
    clusters = scanner._build_clusters(raw_composers, "composer")
    print(f"Before merging: {len(clusters)} clusters")
    for token, cluster in clusters.items():
        print(f"  {token}: {cluster.canonical} ({cluster.occurrences} occurrences)")

    # Merge substring clusters
    merged = scanner._merge_substring_clusters(clusters, "composer")
    print(f"\nAfter merging: {len(merged)} clusters")
    for token, cluster in merged.items():
        print(f"  {token}: {cluster.canonical}")
        print(f"    Variants: {cluster.variants}")
        print(f"    Occurrences: {cluster.occurrences}")

    if len(merged) == 1:
        print("\n✅ SUCCESS: Beethoven variants merged!")
    else:
        print("\n❌ FAIL: Beethoven variants not merged")
        return False

    return True


def test_comma_splitting():
    """Test that comma-separated artists are split."""
    print("\n=== Test 2: Comma-Separated Artist Splitting ===\n")

    settings = Settings.load()
    scanner = IdentityScanner(settings.library, None)

    test_cases = [
        ("David Achenberg, Tana String Quartet", ["David Achenberg", "Tana String Quartet"]),
        ("Bach, J.S.", ["Bach, J.S."]),  # Last, First format - don't split
        ("Yo-Yo Ma", ["Yo-Yo Ma"]),
        ("Miles Davis & John Coltrane", ["Miles Davis", "John Coltrane"]),
    ]

    all_pass = True
    for input_val, expected in test_cases:
        result = scanner._split_names(input_val)
        if result == expected:
            print(f"✅ '{input_val}' → {result}")
        else:
            print(f"❌ '{input_val}' → {result} (expected {expected})")
            all_pass = False

    return all_pass


def test_token_normalization():
    """Test that variants normalize to same token."""
    print("\n=== Test 3: Token Normalization ===\n")

    settings = Settings.load()
    scanner = IdentityScanner(settings.library, None)

    test_cases = [
        ("Art Blakey & The Jazz Messengers", "Art Blakey and The Jazz Messengers", True),
        ("Yo-Yo Ma", "Yo Yo Ma", True),
        ("Beethoven", "Ludwig von Beethoven", False),  # Different tokens!
    ]

    all_pass = True
    for val1, val2, should_match in test_cases:
        token1 = scanner._normalize_token(val1)
        token2 = scanner._normalize_token(val2)
        matches = token1 == token2

        if matches == should_match:
            status = "✅"
        else:
            status = "❌"
            all_pass = False

        print(f"{status} '{val1}' vs '{val2}'")
        print(f"   {token1} vs {token2} ({'match' if matches else 'different'})")

    return all_pass


if __name__ == "__main__":
    print("=" * 60)
    print("Testing Canonical Name Fixes")
    print("=" * 60)

    results = []
    results.append(("Substring Merging", test_substring_merging()))
    results.append(("Comma Splitting", test_comma_splitting()))
    results.append(("Token Normalization", test_token_normalization()))

    print("\n" + "=" * 60)
    print("Test Results")
    print("=" * 60)
    for name, passed in results:
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"{status}: {name}")

    if all(r[1] for r in results):
        print("\n🎉 All tests passed!")
        sys.exit(0)
    else:
        print("\n❌ Some tests failed")
        sys.exit(1)
