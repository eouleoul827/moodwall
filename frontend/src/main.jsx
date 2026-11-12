import "./legacy-polyfills.js";
import React from "react";
import { createRoot } from "react-dom/client";
import "@fontsource/jua";
import "@fontsource/noto-sans-kr/500.css";
import "@fontsource/noto-sans-kr/700.css";
import "@fontsource/noto-sans-kr/900.css";
import App from "./App.jsx";
import "./global.css";

createRoot(document.getElementById("root")).render(<App />);
