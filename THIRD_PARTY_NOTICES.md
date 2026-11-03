# Third-party notices

This application is distributed under GPL-2.0-or-later. The GPL v2 text is
included in LICENSE; redistribution may use version 2 or any later version.

## Solaar voltage table

Copyright (C) 2012-2013 Daniel Pavel.
Copyright (C) 2014-2024 Solaar Contributors.
https://pwr-solaar.github.io/Solaar/

The generic voltage table in providers/voltage.py comes from Solaar's
lib/logitech_receiver/hidpp20.py, estimate_battery_level_percentage, under
GPL-2.0-or-later. The interpolation and Windows/UI code are independently written.
The reviewed source and full license are retained in references/solaar/.
The upstream revision is recorded in references/sources.json.

## Protocol references

libratbag, Copyright 2015 Benjamin Tissoires and Red Hat, Inc, MIT.
The reference snapshots in references/gpw-research/ retain their original
copyright, permission and warranty notices. URLs and hashes are in sources.json.
They are research references, not compiled or loaded by this application.

The protocol implementations are independently written from wire-format facts.
No G HUB, V HUB or LEOBOG ONE executable, DLL or proprietary controller script is
included. Unlicensed and proprietary research snapshots and disassembly excerpts
from the local development workspace have been excluded from this distribution.
references/sources.json also records other historical research sources;
its presence does not mean those snapshots are bundled here.

## Device pictures

ui/assets/ contains transparent product pictures derived with imagegen from
user-supplied device photographs. Depicted brands remain with their owners.
The original photographs and large master images are not bundled.
docs/tray-icon-preview.png is an offline synthetic preview of this program's
own tray renderer; its example values are not actual device readings.

There are no third-party Python runtime dependencies.
