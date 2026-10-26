import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import '@fontsource-variable/golos-text';

import { App } from './app/App';
import './index.css';

const root = document.getElementById('root');
if (!root) throw new Error('The application root element is missing');
createRoot(root).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
