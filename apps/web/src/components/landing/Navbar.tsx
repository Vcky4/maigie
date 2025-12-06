/*
 * Maigie - Your Intelligent Study Companion
 * Copyright (C) 2025 Maigie
 *
 * This program is free software: you can redistribute it and/or modify
 * it under the terms of the GNU Affero General Public License as published
 * by the Free Software Foundation, either version 3 of the License, or
 * (at your option) any later version.
 *
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 * GNU Affero General Public License for more details.
 *
 * You should have received a copy of the GNU Affero General Public License
 * along with this program.  If not, see <https://www.gnu.org/licenses/>.
 */

import React from 'react';
import { motion } from 'framer-motion';
import { Menu, X } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { useAuthStore } from '../../features/auth/store/authStore';

export function Navbar() {
  const [isOpen, setIsOpen] = React.useState(false);
  const navigate = useNavigate();
  const { isAuthenticated, user, logout } = useAuthStore();

  const handleLogout = () => {
    logout();
    navigate('/');
  };

  return (
    <nav className="fixed top-0 w-full z-50 bg-surface/80 backdrop-blur-md border-b border-gray-100">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
        <div className="flex justify-between h-16 items-center">
          <div className="flex-shrink-0 flex items-center" onClick={() => navigate('/')}>
            {/* Logo Image */}
            <img src="/assets/logo.png" alt="Maigie Logo" className="h-8 w-auto" />
            <span className="ml-2 text-2xl font-bold text-primary hidden">maigie</span>
          </div>

          <div className="hidden md:flex items-center space-x-8">
            <a href="#features" className="text-gray-600 hover:text-primary transition-colors">Features</a>
            <a href="#pricing" className="text-gray-600 hover:text-primary transition-colors">Pricing</a>
            <a href="#testimonials" className="text-gray-600 hover:text-primary transition-colors">Testimonials</a>
            {isAuthenticated ? (
              <>
                <button
                  onClick={() => navigate('/dashboard')}
                  className="text-gray-600 hover:text-primary font-medium px-3 py-2 transition-colors"
                >
                  Dashboard
                </button>
                <div className="flex items-center space-x-3">
                  <span className="text-gray-600 text-sm">
                    {user?.name || user?.email}
                  </span>
                  <button
                    onClick={handleLogout}
                    className="bg-primary text-white px-4 py-2 rounded-lg hover:bg-primary/90 transition-all shadow-md hover:shadow-lg"
                  >
                    Logout
                  </button>
                </div>
              </>
            ) : (
              <>
                <button
                  onClick={() => navigate('/login')}
                  className="text-gray-600 hover:text-primary font-medium px-3 py-2 transition-colors"
                >
                  Login
                </button>
                <button onClick={() => navigate('/signup')} className="bg-primary text-white px-4 py-2 rounded-lg hover:bg-primary/90 transition-all shadow-md hover:shadow-lg">
                  Start for Free
                </button>
              </>
            )}
          </div>

          <div className="md:hidden flex items-center">
            <button onClick={() => setIsOpen(!isOpen)} className="text-gray-600 hover:text-primary">
              {isOpen ? <X size={24} /> : <Menu size={24} />}
            </button>
          </div>
        </div>
      </div>

      {/* Mobile menu */}
      {isOpen && (
        <motion.div
          initial={{ opacity: 0, y: -20 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: -20 }}
          className="md:hidden bg-white border-b border-gray-100"
        >
          <div className="px-4 pt-2 pb-4 space-y-1">
            <a href="#features" className="block px-3 py-2 text-gray-600 hover:text-primary hover:bg-gray-50 rounded-md">Features</a>
            <a href="#pricing" className="block px-3 py-2 text-gray-600 hover:text-primary hover:bg-gray-50 rounded-md">Pricing</a>
            <a href="#testimonials" className="block px-3 py-2 text-gray-600 hover:text-primary hover:bg-gray-50 rounded-md">Testimonials</a>
            <div className="pt-4 space-y-2">
              {isAuthenticated ? (
                <>
                  <div className="px-3 py-2 text-gray-600 text-sm border-b border-gray-200">
                    {user?.name || user?.email}
                  </div>
                  <button
                    onClick={() => {
                      navigate('/dashboard');
                      setIsOpen(false);
                    }}
                    className="w-full text-left px-3 py-2 text-gray-600 hover:text-primary font-medium"
                  >
                    Dashboard
                  </button>
                  <button
                    onClick={() => {
                      handleLogout();
                      setIsOpen(false);
                    }}
                    className="w-full bg-primary text-white px-3 py-2 rounded-lg hover:bg-primary/90 shadow-sm"
                  >
                    Logout
                  </button>
                </>
              ) : (
                <>
                  <button
                    onClick={() => {
                      navigate('/login');
                      setIsOpen(false);
                    }}
                    className="w-full text-left px-3 py-2 text-gray-600 hover:text-primary font-medium"
                  >
                    Login
                  </button>
                  <button
                    onClick={() => {
                      navigate('/signup');
                      setIsOpen(false);
                    }}
                    className="w-full bg-primary text-white px-3 py-2 rounded-lg hover:bg-primary/90 shadow-sm"
                  >
                    Start for Free
                  </button>
                </>
              )}
            </div>
          </div>
        </motion.div>
      )}
    </nav>
  );
}
