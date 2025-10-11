// src/dev/SpritePlayground.tsx
import React from 'react';
import { WhimsySprite } from '../components/loading/WhimsySprite';

export function SpritePlayground() {
  // Primary theme color -> wired to --color-primary (accepts "#hex" or "r g b")
  const [primary, setPrimary] = React.useState<string>('79 70 229'); // same default as app

  // SuperBalls exposed knobs
  const [size, setSize] = React.useState<number>(40);     // px (≈ previous 2.5rem)
  const [speed, setSpeed] = React.useState<number>(1.6);  // 0..2.0 is a comfy range

  // Reduced motion override (runtime toggle)
  const [reduced, setReduced] = React.useState<boolean>(false);

  // ————— Theme color wiring —————
  React.useEffect(() => {
    // Accept "#RRGGBB" or "r g b" triplet, store as "r g b" in --color-primary
    const toTriplet = (v: string) => {
      const hex = v.trim().toLowerCase();
      if (/^#([0-9a-f]{6})$/.test(hex)) {
        const r = parseInt(hex.slice(1, 3), 16);
        const g = parseInt(hex.slice(3, 5), 16);
        const b = parseInt(hex.slice(5, 7), 16);
        return `${r} ${g} ${b}`;
      }
      // If user enters a triplet, keep it (we also tolerate commas)
      const t = v.trim().replace(/,/g, ' ');
      if (/^\d+\s+\d+\s+\d+$/.test(t)) return t;
      // Fallback to default if invalid
      return '79 70 229';
    };

    const triplet = toTriplet(primary);
    document.documentElement.style.setProperty('--color-primary', triplet);
  }, [primary]);

  // ————— Reduced motion override —————
  React.useEffect(() => {
    // Our WhimsySprite hook looks for this attr/flag to force/allow motion
    (window as any).__ALLOW_MOTION__ = !reduced;
    const root = document.documentElement;
    if (reduced) {
      root.removeAttribute('data-allow-motion');
    } else {
      root.setAttribute('data-allow-motion', 'true');
    }
  }, [reduced]);

  return (
    <div className="min-h-screen bg-white text-slate-800 p-6">
      <h1 className="text-2xl font-semibold mb-4">Sprite Playground</h1>

      <div className="flex flex-col lg:flex-row gap-8 items-start">
        {/* Live preview */}
        <div className="flex flex-col items-center gap-4 p-8 rounded-2xl border border-slate-200 shadow-sm">
          <div className="text-sm text-slate-500">Live loader</div>
          {/* Pass size/speed as class vars so WhimsySprite can read them if needed */}
          <div
            style={
              {
                // purely visual container to keep consistent spacing
                '--play-size': `${size}px`,
              } as React.CSSProperties
            }
          >
            {/* WhimsySprite reads color from --color-primary and reduced motion via hook.
                It also accepts size/speed props if you extended it; if not, we can just
                scale via a wrapper. For simplicity, set a fixed box and scale via inline style. */}
            <div style={{ width: size, height: size }}>
              <WhimsySprite className="loading-strip" />
            </div>
          </div>
          <div className="text-xs text-slate-500">
            Color: <code>--color-primary</code>, Size: {size}px, Speed: {speed}
          </div>
        </div>

        {/* Controls */}
        <form className="space-y-5 w-full max-w-md">
          {/* Color */}
          <div className="flex items-center gap-3">
            <label className="w-40 text-sm">Primary color</label>
            <div className="flex items-center gap-2">
              <input
                className="border rounded px-2 py-1 w-[180px]"
                value={primary}
                onChange={(e) => setPrimary(e.target.value)}
                placeholder="#4F46E5 or 79 70 229"
              />
              {/* Handy color input to quickly try hues */}
              <input
                type="color"
                aria-label="Pick color"
                className="w-8 h-8 p-0 border rounded"
                value={
                  // try to convert current primary to hex for the color input
                  (() => {
                    const t = primary.trim().replace(/,/g, ' ');
                    if (/^\d+\s+\d+\s+\d+$/.test(t)) {
                      const [r, g, b] = t.split(/\s+/).map(Number);
                      const toHex = (n: number) => n.toString(16).padStart(2, '0');
                      return `#${toHex(r)}${toHex(g)}${toHex(b)}`;
                    }
                    if (/^#([0-9a-f]{6})$/i.test(t)) return t;
                    return '#4f46e5';
                  })()
                }
                onChange={(e) => setPrimary(e.target.value)}
              />
            </div>
          </div>

          {/* Size */}
          <div className="flex items-center gap-3">
            <label className="w-40 text-sm">Size (px)</label>
            <input
              type="range"
              min={24}
              max={72}
              step={1}
              value={size}
              onChange={(e) => setSize(parseInt(e.target.value, 10))}
              className="w-[180px]"
            />
            <span className="text-sm w-10 text-right">{size}</span>
          </div>

          {/* Speed */}
          <div className="flex items-center gap-3">
            <label className="w-40 text-sm">Speed</label>
            <input
              type="range"
              min={0}
              max={2.5}
              step={0.1}
              value={speed}
              onChange={(e) => setSpeed(parseFloat(e.target.value))}
              className="w-[180px]"
            />
            <span className="text-sm w-10 text-right">{speed.toFixed(1)}</span>
          </div>

          {/* Reduced motion */}
          <div className="flex items-center gap-3">
            <label className="w-40 text-sm">Reduced motion</label>
            <input
              type="checkbox"
              checked={reduced}
              onChange={(e) => setReduced(e.target.checked)}
            />
            <span className="text-xs text-slate-500">
              (Pauses animation immediately)
            </span>
          </div>

          <div className="text-xs text-slate-500 pt-2">
            Tip: the live loader uses your theme color via <code>--color-primary</code>.  
            Size & speed are local to this playground.
          </div>
        </form>
      </div>
    </div>
  );
}
