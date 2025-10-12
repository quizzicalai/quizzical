// frontend/src/dev/result.tsx
import React from 'react';
import { createRoot } from 'react-dom/client';
import '../index.css';
import { ResultPreview } from './ResultPreview';

const root = createRoot(document.getElementById('root')!);
root.render(<ResultPreview />);
