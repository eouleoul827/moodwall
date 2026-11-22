import { useMemo } from "react";
import { generateBackground } from "./generator.js";
import "./moodBackground.css";

// The SVG is built entirely from our own templates; mention text is only
// used for keyword lookup, so injecting it as markup is safe.
export default function MoodBackground({ emotion, content, seed, className = "" }) {
  const { svg } = useMemo(
    () => generateBackground({ emotion, content, seed: String(seed ?? "") }),
    [emotion, content, seed],
  );
  return <div className={`mw-bg ${className}`.trim()} dangerouslySetInnerHTML={{ __html: svg }} />;
}
