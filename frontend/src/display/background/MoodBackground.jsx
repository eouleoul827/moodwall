import { useEffect, useMemo, useRef, useState } from "react";
import { generateBackground } from "./generator.js";
import "./moodBackground.css";

// Images live next to this file so Vite puts them under /assets/ with hashed
// names; the backend serves only /assets/ from the build.
const MASCOT_URLS = Object.fromEntries(
  Object.entries(import.meta.glob("./mascot/*.webp", { eager: true, query: "?url", import: "default" }))
    .map(([path, url]) => [path.slice(path.lastIndexOf("/") + 1), url]),
);
const assetUrl = (file) => MASCOT_URLS[file] ?? "";

// The SVG is built entirely from our own templates; mention text is only
// used for keyword lookup, so injecting it as markup is safe.
export default function MoodBackground({ emotion, content, seed, side, className = "" }) {
  const box = useRef(null);
  const [aspect, setAspect] = useState(16 / 9);

  // Draw the scene at the box's own ratio so a 2046 × 768 panel is not cropped.
  useEffect(() => {
    const el = box.current;
    if (!el || typeof ResizeObserver === "undefined") return undefined;
    const update = () => {
      const { width, height } = el.getBoundingClientRect();
      if (width > 0 && height > 0) setAspect(Math.round((width / height) * 10) / 10);
    };
    update();
    const observer = new ResizeObserver(update);
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  const { svg } = useMemo(
    () => generateBackground({ emotion, content, seed: String(seed ?? ""), side, aspect, assetUrl }),
    [emotion, content, seed, side, aspect],
  );
  return <div ref={box} className={`mw-bg ${className}`.trim()} aria-hidden="true" dangerouslySetInnerHTML={{ __html: svg }} />;
}
