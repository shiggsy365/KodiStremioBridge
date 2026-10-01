# Badge sources

`Nuvio.json` is a Nuvio stream-badge configuration shared by saif1233:
https://gist.github.com/saif1233/a2b9817bb8a632ae93a6076c1e1459af (revision f61d444e).

`images/` holds copies of the badge artwork it links to, renamed to the picker's badge keys.
They come from these GitHub repositories, which don't state a licence:

- https://github.com/9mousaa/BetterFormatter (`main/images/`)
- https://github.com/nobnobz/Omni-Template-Bot-Bid-Raiser (`main/Other/regex tags/`)

Several are logos of their owners (Dolby, DTS, IMAX). `tools/make_badges.py` builds the
picker's badges from these and the colours in `Nuvio.json`.

| Badge key | Nuvio rule | Original URL |
|---|---|---|
| `rel_remux` | Remux | https://raw.githubusercontent.com/9mousaa/BetterFormatter/main/images/colored-remux.png |
| `rel_bluray` | BluRay | https://raw.githubusercontent.com/9mousaa/BetterFormatter/main/images/colored-bluray.png |
| `rel_webdl` | WebDL | https://raw.githubusercontent.com/9mousaa/BetterFormatter/main/images/colored-webdl.png |
| `extra_seadex` | SeaDex | https://raw.githubusercontent.com/9mousaa/BetterFormatter/main/images/colored-SeaDex.png |
| `res_4k` | 4K | https://raw.githubusercontent.com/nobnobz/Omni-Template-Bot-Bid-Raiser/main/Other/regex%20tags/4k.png |
| `res_1080p` | 1080p | https://raw.githubusercontent.com/nobnobz/Omni-Template-Bot-Bid-Raiser/main/Other/regex%20tags/1080p.png |
| `res_720p` | 720p | https://raw.githubusercontent.com/nobnobz/Omni-Template-Bot-Bid-Raiser/main/Other/regex%20tags/720p.png |
| `vis_dv` | DV | https://raw.githubusercontent.com/nobnobz/Omni-Template-Bot-Bid-Raiser/main/Other/regex%20tags/DV.png |
| `vis_hdr10plus` | HDR10+ | https://raw.githubusercontent.com/nobnobz/Omni-Template-Bot-Bid-Raiser/main/Other/regex%20tags/HDR10Plus.png |
| `vis_hdr10` | HDR10 | https://raw.githubusercontent.com/nobnobz/Omni-Template-Bot-Bid-Raiser/main/Other/regex%20tags/HDR10.png |
| `vis_hdr` | HDR | https://raw.githubusercontent.com/nobnobz/Omni-Template-Bot-Bid-Raiser/main/Other/regex%20tags/HDR.png |
| `extra_imax_enhanced` | IMAX Enhanced | https://raw.githubusercontent.com/9mousaa/BetterFormatter/main/images/IMAX-enhanced.png |
| `extra_imax` | IMAX | https://github.com/nobnobz/Omni-Template-Bot-Bid-Raiser/blob/main/Other/regex%20tags/IMAXv2.PNG?raw=true |
| `aud_atmos_dv` | Atmos+DV (off) | https://raw.githubusercontent.com/9mousaa/BetterFormatter/main/images/atmos-vision.png |
| `aud_truehd` | TrueHD | https://raw.githubusercontent.com/nobnobz/Omni-Template-Bot-Bid-Raiser/main/Other/regex%20tags/TrueHD.png |
| `aud_atmos` | Atmos | https://raw.githubusercontent.com/nobnobz/Omni-Template-Bot-Bid-Raiser/main/Other/regex%20tags/Atmos.png |
| `aud_truehd_dv` | TrueHD+DV (off) | https://raw.githubusercontent.com/9mousaa/BetterFormatter/main/images/truehd-vision.png |
| `aud_ddp_dv` | DD++DV (off) | https://raw.githubusercontent.com/9mousaa/BetterFormatter/main/images/digitalplus-vision.png |
| `aud_dtsx` | DTS:X | https://raw.githubusercontent.com/nobnobz/Omni-Template-Bot-Bid-Raiser/main/Other/regex%20tags/dtsx.png |
| `aud_dtshdma` | DTS-HD MA | https://raw.githubusercontent.com/nobnobz/Omni-Template-Bot-Bid-Raiser/main/Other/regex%20tags/dtsHDMA.png |
| `aud_dtshd` | DTS-HD | https://raw.githubusercontent.com/nobnobz/Omni-Template-Bot-Bid-Raiser/main/Other/regex%20tags/dtsHD.png |
| `aud_dts` | DTS | https://raw.githubusercontent.com/nobnobz/Omni-Template-Bot-Bid-Raiser/main/Other/regex%20tags/dts.png |
| `aud_ddp` | DD+ | https://raw.githubusercontent.com/nobnobz/Omni-Template-Bot-Bid-Raiser/main/Other/regex%20tags/DDPLUS.png |
| `aud_dd_dv` | DD+DV (off) | https://raw.githubusercontent.com/9mousaa/BetterFormatter/main/images/digital-vision.png |
| `aud_dd` | DD | https://raw.githubusercontent.com/nobnobz/Omni-Template-Bot-Bid-Raiser/main/Other/regex%20tags/DD.png |
| `ch_71` | 7.1 | https://raw.githubusercontent.com/nobnobz/Omni-Template-Bot-Bid-Raiser/main/Other/regex%20tags/71.png |
| `ch_61` | 6.1 | https://raw.githubusercontent.com/nobnobz/Omni-Template-Bot-Bid-Raiser/main/Other/regex%20tags/61.png |
| `ch_51` | 5.1 | https://raw.githubusercontent.com/nobnobz/Omni-Template-Bot-Bid-Raiser/main/Other/regex%20tags/51.png |
