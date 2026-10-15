import React, { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Layers, Loader2, AlertCircle } from 'lucide-react';
import { supabase } from '@/services/supabase';
import { setUseMockMode } from '@/services/api';

export const AuthCallbackPage: React.FC = () => {
  const navigate = useNavigate();
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  useEffect(() => {
    const handleAuthCallback = async () => {
      // 1. Inspect URL search params and hash fragment for errors
      const searchParams = new URLSearchParams(window.location.search);
      const hashParams = new URLSearchParams(
        window.location.hash.startsWith('#') ? window.location.hash.substring(1) : window.location.hash
      );

      const error = searchParams.get('error') || hashParams.get('error');
      const errorDescription = searchParams.get('error_description') || hashParams.get('error_description');

      if (error || errorDescription) {
        const msg = errorDescription || error || 'OAuth authentication failed.';
        setErrorMessage(msg);
        setTimeout(() => {
          navigate(`/login?error=${encodeURIComponent(msg)}`, { replace: true });
        }, 2000);
        return;
      }

      // 2. Wait for Supabase to parse and persist the session
      try {
        const { data: { session }, error: sessionError } = await supabase.auth.getSession();

        if (sessionError) {
          setErrorMessage(sessionError.message);
          setTimeout(() => {
            navigate(`/login?error=${encodeURIComponent(sessionError.message)}`, { replace: true });
          }, 2000);
          return;
        }

        if (session) {
          localStorage.removeItem('aura_demo_session');
          setUseMockMode(false);
          navigate('/dashboard', { replace: true });
          return;
        }

        // 3. Fallback: subscribe to onAuthStateChange if session resolution is still in flight
        const { data: { subscription } } = supabase.auth.onAuthStateChange((event, newSession) => {
          if (newSession && (event === 'SIGNED_IN' || event === 'TOKEN_REFRESHED')) {
            localStorage.removeItem('aura_demo_session');
            setUseMockMode(false);
            subscription.unsubscribe();
            navigate('/dashboard', { replace: true });
          }
        });

        // 4. Timeout guard if no session is received
        const timer = setTimeout(() => {
          subscription.unsubscribe();
          navigate('/login?error=Session%20could%20not%20be%20restored%20from%20callback.', { replace: true });
        }, 4000);

        return () => {
          clearTimeout(timer);
          subscription.unsubscribe();
        };
      } catch (err: any) {
        setErrorMessage(err.message || 'Unexpected authentication callback error.');
        setTimeout(() => {
          navigate('/login?error=Unexpected%20callback%20error.', { replace: true });
        }, 2000);
      }
    };

    handleAuthCallback();
  }, [navigate]);

  return (
    <div className="min-h-screen bg-slate-50 flex flex-col justify-center items-center px-4">
      <div className="max-w-md w-full text-center space-y-6">
        <div className="w-16 h-16 rounded-2xl bg-gradient-to-tr from-aura-navy to-aura-deep flex items-center justify-center text-white shadow-card mx-auto">
          <Layers className="w-9 h-9 text-aura-cyan" />
        </div>

        {errorMessage ? (
          <div className="p-4 bg-rose-50 border border-rose-200 rounded-xl text-xs text-rose-700 flex items-center gap-3 text-left">
            <AlertCircle className="w-5 h-5 text-rose-600 shrink-0" />
            <div>
              <p className="font-bold">Authentication Error</p>
              <p>{errorMessage}</p>
              <p className="text-[10px] text-slate-500 mt-1">Redirecting to login...</p>
            </div>
          </div>
        ) : (
          <div className="space-y-3">
            <Loader2 className="w-8 h-8 text-aura-cyan animate-spin mx-auto" />
            <h2 className="text-lg font-bold text-aura-navy">Authenticating Workspace</h2>
            <p className="text-xs text-slate-500">
              Verifying your OAuth provider session and restoring credentials...
            </p>
          </div>
        )}
      </div>
    </div>
  );
};
