# KaraFun Integration Findings

Date: 2026-07-11

## Summary

The safest supported integration mode for SingWS is **Mode 3: Assisted KaraFun workflow**.

KaraFun's public documentation describes its own apps, web player, Pro subscription, Business offering, offline catalog features inside KaraFun, and contact channels for Pro/Business questions. I did not find a public official third-party playback API, SDK, OAuth flow, embeddable player, remote-control protocol, `.kfn` decoder, or documented media-file specification that would let SingWS directly search, authenticate, stream, decrypt, cache, or play KaraFun catalog content.

## Official Sources Checked

- KaraFun home/plans page: https://www.karafun.com/
- KaraFun apps/download page: https://www.karafun.com/apps/
- KaraFun Pro page: https://www.karafun.com/pro/
- KaraFun Terms of Service: https://www.karafun.com/terms.html
- KaraFun contact page: https://www.karafun.com/support/contact.html

## What Public Documentation Supports

- KaraFun provides official apps for computers, mobile/tablets, TVs, and web player use.
- KaraFun Pro is positioned for professional public events, with queue control, singer rotation, custom key/tempo, background music, offline catalog inside KaraFun, and public venue licensing details.
- KaraFun Business is a separate offer distributed by Recisio; public terms direct Business inquiries to KaraFun/Recisio.
- KaraFun terms say subscription content is temporary access through the service, access ends when the subscription ends, and content cannot be downloaded/transferred/copied except where professional offers explicitly allow it.
- KaraFun terms identify the service's digital files as protected and prohibit attempts to circumvent technological protection measures.

## What Was Not Found

- Public catalog/search API.
- Partner or business API documentation.
- Public SDK.
- Embeddable player for third-party applications.
- OAuth or delegated authorization flow for third-party apps.
- Supported remote-control protocol for launching and tracking a specific song in the KaraFun app.
- Authorized `.kfn` playback library, file format specification, or decoder.
- Official permission for third-party PCM access, audio processing, or video/lyrics rendering inside SingWS.

## Implementation Decision

Until KaraFun provides a written partner integration or public API/SDK, SingWS must not implement direct KaraFun streaming playback. It must not inspect protected `.kfn` contents, reverse-engineer authentication, scrape private APIs, cache/decrypt/convert tracks, capture protected audio/video, or treat a Pro subscription as permission for third-party playback.

The codebase now has a provider foundation that can store safe KaraFun references and keep provider metadata separate from local media:

- `local`
- `karafun_local`
- `karafun_streaming`
- `external_karafun`

In the current supported mode, KaraFun items are externally controlled references. SingWS may store the title, artist, KaraFun track ID or URL supplied by the host, and open/display that reference for manual use in the official KaraFun app. Playback completion must remain host-confirmed until KaraFun provides an authorized event/control channel.

## Features Blocked Without KaraFun Cooperation

- Direct KaraFun catalog search from SingWS.
- KaraFun account login inside SingWS.
- Subscription validation inside SingWS.
- Streaming playback through SingWS.
- `.kfn` decoding or local playback.
- Audio routing through SingWS's engine.
- SingWS key/tempo/EQ/normalization/VST processing for KaraFun tracks.
- Automatic playback completion detection.
- Official lyric/video rendering on the SingWS show screen.
- Secure token storage for KaraFun auth, because no supported third-party auth flow was found.

## Next Recommended Step

Contact KaraFun/Recisio through Pro or Business channels and request written documentation for one of:

- A partner catalog/search/playback API.
- An embeddable player or SDK.
- A supported deep-link/remote-control protocol with playback completion events.
- A documented `.kfn` playback component or file specification licensed for third-party apps.

If KaraFun provides one, SingWS can wire a real provider implementation behind the new generic playback-provider interface without spreading KaraFun-specific logic through the app.
