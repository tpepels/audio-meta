[] Do a pre-run to determine the canonical names of each individual artist and composer to avoid directories and meta tags with divergent spellings
[] Check discogs during singleton runs for Single releases and determine a match.
[] If only 1 suggestion during defferred user input then try to determine whether this is the correct match on different criteria

[] When looking at singletons, if they have a track number in their filename and/or meta information, they must be accidentally left somewhere. Then have to look at artist/composer information to figure out which album/relase ON DISK has a missing corresponding track. This could be more than 1 directory, but that's unlikely. In any case this is what then should be presented and asked from the user.

[] Also during singleton checking, we should use the fingerprint to determine likely releases. 

[] I think that singleton matching should also make use of the pipeline but in a slightly different workflow, i.e. to find out where they belong. Couldn't we write plugins for them too and map a custom pipeline? Then we can use MB and DG to figure out what the problem is. In many cases I see a track number, so it was originally misplaced. Then it turns out that the release that they belonged to was moved without them (i.e. they're home alone's kevins).