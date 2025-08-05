import React, { useEffect, useRef } from 'react';
import { useConfig } from '../context/ConfigContext';
import type { StaticContentBlock, StaticPageKey } from '../types/pages';
import { Spinner } from '../components/common/Spinner';

interface BlockRendererProps {
  block: StaticContentBlock;
}

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

interface StaticPageProps {
  pageKey: StaticPageKey;
}

/**
 * Renders a static page's content (e.g., About, Terms) from the configuration.
 * This component is now content-only and relies on a parent layout for the header and footer.
 */
export const StaticPage: React.FC<StaticPageProps> = ({ pageKey }) => {
  const { config } = useConfig();
  const headingRef = useRef<HTMLHeadingElement>(null);

  useEffect(() => {
    headingRef.current?.focus();
  }, [pageKey]);

  if (!config) {
    return <Spinner message="Loading..." />;
  }

  const pageContent = config.content[pageKey];

  if (!pageContent) {
    return (
      <main className="flex-grow text-center py-10">
        <h1 className="text-2xl font-bold">Content Not Available</h1>
        <p className="text-muted">This page's content could not be loaded.</p>
      </main>
    );
  }

  return (
    <main className="flex-grow max-w-3xl mx-auto px-4 py-10">
      <article className="prose dark:prose-invert max-w-none">
        <h1 ref={headingRef} tabIndex={-1} className="text-3xl font-bold mb-6 outline-none">
          {pageContent.title}
        </h1>
        {pageContent.blocks.map((block, index) => (
          <BlockRenderer key={index} block={block as StaticContentBlock} />
        ))}
      </article>
    </main>
  );
};
