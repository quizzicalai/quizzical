// src/pages/StaticPage.tsx
import React, { useEffect, useRef } from 'react';
import { useConfig } from '../context/ConfigContext';
import { Header } from '../components/layout/Header';
import { Footer } from '../components/layout/Footer';
import type { StaticContentBlock, StaticPageKey } from '../types/pages';

// Props for the BlockRenderer component
interface BlockRendererProps {
  block: StaticContentBlock;
}

// A map to render different block types
const BlockRenderer: React.FC<BlockRendererProps> = ({ block }) => {
  switch (block.type) {
    case 'p':
      return <p className="mb-4">{block.text}</p>;
    case 'h2':
      return <h2 className="text-2xl font-semibold mt-6 mb-3">{block.text}</h2>;
    case 'ul':
      return (
        <ul className="list-disc list-inside mb-4 pl-4">
          {block.items.map((item, index) => <li key={index}>{item}</li>)}
        </ul>
      );
    case 'ol':
      return (
        <ol className="list-decimal list-inside mb-4 pl-4">
          {block.items.map((item, index) => <li key={index}>{item}</li>)}
        </ol>
      );
    default:
      return null;
  }
};

// Props for the StaticPage component
interface StaticPageProps {
  pageKey: StaticPageKey;
}

/**
 * Renders a static page (e.g., About, Terms) from the configuration.
 */
export const StaticPage: React.FC<StaticPageProps> = ({ pageKey }) => {
  const { config } = useConfig();
  const headingRef = useRef<HTMLHeadingElement>(null);
  const pageContent = config?.content?.[pageKey];

  useEffect(() => {
    // Focus the heading for accessibility when the page loads
    headingRef.current?.focus();
  }, [pageKey]);

  if (!pageContent) {
    return (
      <div className="flex flex-col min-h-screen">
        <Header />
        <main className="flex-grow text-center py-10">
          <h1 className="text-2xl font-bold">Content Not Available</h1>
          <p className="text-muted">This page's content could not be loaded.</p>
        </main>
        <Footer />
      </div>
    );
  }

  return (
    <div className="flex flex-col min-h-screen">
      <Header />
      <main className="flex-grow max-w-3xl mx-auto px-4 py-10">
        <article className="prose max-w-none">
          <h1 ref={headingRef} tabIndex={-1} className="text-3xl font-bold mb-6 outline-none">
            {pageContent.title}
          </h1>
          {pageContent.blocks.map((block, index) => (
            <BlockRenderer key={index} block={block} />
          ))}
        </article>
      </main>
      <Footer />
    </div>
  );
}
