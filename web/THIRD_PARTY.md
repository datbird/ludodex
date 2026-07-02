# Third-party credits

Ludodex bundles or adapts the following third-party work. Each is used under the
terms of its license, reproduced/summarized below.

---

## CodeFronts — "Particle Burst" CSS tabs

- **Source:** https://codefronts.com/navigation/css-tabs/particle-burst/
- **Author:** CodeFronts (https://codefronts.com)
- **License:** MIT

Used in Ludodex as the animated tab bar (sliding accent underline + an 8-spark
burst on selection). The original vanilla HTML/CSS/JS was ported to a reusable
React component (`ParticleTabs` in `web/src/App.tsx`) and its styles adapted
(`.pt-*` rules in `web/src/App.css`) — the underline colour is themed to the app
accent while the spark palette is CodeFronts' original. The animation technique
(per-particle trajectory via CSS custom properties `--dx`/`--dy`, JS spawning the
sparks at the click point) is unchanged.

### MIT License

```
MIT License

Copyright (c) CodeFronts (https://codefronts.com)

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```
