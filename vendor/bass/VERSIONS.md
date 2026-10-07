# BASS runtime pins

The macOS app bundles these official universal libraries from
<https://www.un4seen.com/bass.html>. Each contains x86_64 and arm64 slices with
a macOS 11.0 deployment target, so they remain compatible with SingWS's macOS
12.3 floor.

| Library | Version | Official archive SHA-256 | Bundled dylib SHA-256 |
| --- | --- | --- | --- |
| BASS | 2.4.18.3 | Existing official distribution | See repository object |
| BASSmix | 2.4.13 | `66b41300bed9868c203950cbec7641e89a56a088931e6b8125bf3011149cee69` | `1ed07cdccb8d0ec6bed0c6dbeadd2066a300816ec4def29ba64a52e85cefc9fe` |
| BASSFLAC | 2.4.6.1 | `866f03fa23dfce1036930a8dbec9a3c51028d36f53a1b891689346e2b46275ad` | `8efe6c0e372328708f6926b15748f01002da7eba01c790e306121c9853201de2` |

Download URLs:

- <https://www.un4seen.com/files/bassmix24-osx.zip>
- <https://www.un4seen.com/files/bassflac24-osx.zip>

BASS is proprietary. Keep the upstream text files with the dylibs and ensure
the SingWS distribution model is covered by the appropriate BASS license.
