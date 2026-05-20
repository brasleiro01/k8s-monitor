import React from 'react';
import ReactDOM from 'react-dom/client';
import { GoogleOAuthProvider } from '@react-oauth/google';
import App from './App.jsx';
import './App.css';

fetch('/api/config')
  .then(r => r.json())
  .catch(() => ({}))
  .then(config => {
    const clientId = config.googleClientId || '';
    ReactDOM.createRoot(document.getElementById('root')).render(
      <React.StrictMode>
        <GoogleOAuthProvider clientId={clientId}>
          <App googleClientId={clientId} />
        </GoogleOAuthProvider>
      </React.StrictMode>
    );
  });
