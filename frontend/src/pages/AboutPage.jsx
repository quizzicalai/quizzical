// src/pages/AboutPage.jsx
import React, { useEffect, useRef } from 'react';
import { useConfig } from '../context/ConfigContext';

export function AboutPage() {
  const { config } = useConfig();
  const headingRef = useRef(null);
  const content = config?.content?.aboutPage ?? {};

  useEffect(() => {
    headingRef.current?.focus();
  }, []);

  return (
    <main className="max-w-3xl mx-auto px-4 py-10">
      <h1
        ref={headingRef}
        tabIndex={-1}
        className="text-3xl font-bold text-fg mb-4 outline-none"
      >
        {content.heading || 'About Us'}
      </h1>
      <div className="prose max-w-none text-text-color/90">
        <p>
          {content.body || 'This is the about page for Quizzical. Content can be managed from the backend configuration.'}
        </p>
        {content.links && (
          <ul>
            {content.links.map((link, i) => (
              <li key={i}>
                <a
                  href={link.href}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-primary-color hover:underline"
                >
                  {link.label}
                </a>
              </li>
            ))}
          </ul>
        )}
      </div>
    </main>
  );
}