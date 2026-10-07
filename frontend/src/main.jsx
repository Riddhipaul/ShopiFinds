import React from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import Products from './pages/Products.jsx';
import Collection from './pages/Collection.jsx';
import Admin from './pages/Admin.jsx';
import './styles.css';

createRoot(document.getElementById('root')).render(
  <BrowserRouter>
    <Routes>
      <Route path="/products" element={<Products />} />
      <Route path="/collection/:id" element={<Collection />} />
      <Route path="/admin" element={<Admin />} />
      <Route path="*" element={<Navigate to="/products" replace />} />
    </Routes>
  </BrowserRouter>
);
