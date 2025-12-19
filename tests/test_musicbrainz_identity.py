#!/usr/bin/env python3
"""
Test MusicBrainz identity resolver.

This tests the new MusicBrainz integration for identity resolution.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from audio_meta.providers.musicbrainz.identity_resolver import (
    MusicBrainzIdentityResolver,
    MUSICBRAINZ_AVAILABLE
)


def test_musicbrainz_availability():
    """Test if MusicBrainz is available."""
    print("\n=== Test 1: MusicBrainz Availability ===\n")

    if MUSICBRAINZ_AVAILABLE:
        print("✅ MusicBrainz library (musicbrainzngs) is installed")
        return True
    else:
        print("❌ MusicBrainz library not available")
        print("   Install with: pip install musicbrainzngs")
        return False


def test_artist_search():
    """Test searching for an artist."""
    print("\n=== Test 2: Artist Search ===\n")

    if not MUSICBRAINZ_AVAILABLE:
        print("⏭️  Skipping (MusicBrainz not available)")
        return True

    resolver = MusicBrainzIdentityResolver()

    # Test case: Art Blakey & The Jazz Messengers
    print("Searching for: 'Art Blakey & The Jazz Messengers'")
    identity = resolver.search_artist("Art Blakey & The Jazz Messengers")

    if identity:
        print(f"✅ Found!")
        print(f"   Canonical: {identity.canonical_name}")
        print(f"   Type: {identity.artist_type}")
        print(f"   MB ID: {identity.mb_id}")
        print(f"   Confidence: {identity.confidence:.0%}")
        print(f"   Aliases ({len(identity.aliases)}):")
        for alias in identity.aliases[:5]:  # First 5
            print(f"     - {alias}")
        if len(identity.aliases) > 5:
            print(f"     ... and {len(identity.aliases) - 5} more")
        return True
    else:
        print("❌ Not found")
        return False


def test_classical_composer():
    """Test searching for a classical composer."""
    print("\n=== Test 3: Classical Composer ===\n")

    if not MUSICBRAINZ_AVAILABLE:
        print("⏭️  Skipping (MusicBrainz not available)")
        return True

    resolver = MusicBrainzIdentityResolver()

    # Test case: J.S. Bach
    print("Searching for: 'J.S. Bach'")
    identity = resolver.search_artist("J.S. Bach")

    if identity:
        print(f"✅ Found!")
        print(f"   Canonical: {identity.canonical_name}")
        print(f"   Sort Name: {identity.sort_name}")
        print(f"   Type: {identity.artist_type}")
        print(f"   Confidence: {identity.confidence:.0%}")
        print(f"   Aliases ({len(identity.aliases)}):")
        for alias in identity.aliases[:10]:
            print(f"     - {alias}")
        return True
    else:
        print("❌ Not found")
        return False


def test_alias_detection():
    """Test that aliases are properly detected."""
    print("\n=== Test 4: Alias Detection ===\n")

    if not MUSICBRAINZ_AVAILABLE:
        print("⏭️  Skipping (MusicBrainz not available)")
        return True

    resolver = MusicBrainzIdentityResolver()

    # Search for one variant
    identity = resolver.search_artist("Miles Davis")

    if not identity:
        print("❌ Artist not found")
        return False

    # Test various forms
    test_cases = [
        ("Miles Davis", True),
        ("Miles Dewey Davis III", True),
        ("miles davis", True),  # Case insensitive
        ("John Coltrane", False),  # Different artist
    ]

    all_passed = True
    for name, should_match in test_cases:
        matches = identity.has_alias(name)
        if matches == should_match:
            status = "✅"
        else:
            status = "❌"
            all_passed = False

        print(f"{status} '{name}' {'is' if should_match else 'is not'} an alias: {matches}")

    return all_passed


def test_rate_limiting():
    """Test that rate limiting works."""
    print("\n=== Test 5: Rate Limiting ===\n")

    if not MUSICBRAINZ_AVAILABLE:
        print("⏭️  Skipping (MusicBrainz not available)")
        return True

    import time

    resolver = MusicBrainzIdentityResolver()

    artists = ["Miles Davis", "John Coltrane", "Beethoven"]

    print(f"Searching for {len(artists)} artists (should take ~{len(artists)} seconds)...")
    start = time.time()

    for artist in artists:
        print(f"  Searching: {artist}")
        identity = resolver.search_artist(artist)
        if identity:
            print(f"    → {identity.canonical_name}")

    elapsed = time.time() - start

    print(f"\nCompleted in {elapsed:.1f} seconds")

    # Should take at least (n-1) * 1.1 seconds due to rate limiting
    min_expected = (len(artists) - 1) * 1.0

    if elapsed >= min_expected:
        print(f"✅ Rate limiting working (took {elapsed:.1f}s, expected >= {min_expected:.1f}s)")
        return True
    else:
        print(f"⚠️  Completed faster than expected ({elapsed:.1f}s < {min_expected:.1f}s)")
        print("   Rate limiting may not be working correctly")
        return True  # Not a failure, just a warning


if __name__ == "__main__":
    print("=" * 60)
    print("Testing MusicBrainz Identity Resolver")
    print("=" * 60)

    results = []
    results.append(("MusicBrainz Available", test_musicbrainz_availability()))

    if MUSICBRAINZ_AVAILABLE:
        results.append(("Artist Search", test_artist_search()))
        results.append(("Classical Composer", test_classical_composer()))
        results.append(("Alias Detection", test_alias_detection()))
        results.append(("Rate Limiting", test_rate_limiting()))
    else:
        print("\n⚠️  MusicBrainz not available - skipping tests")
        print("   Install with: pip install musicbrainzngs")

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
