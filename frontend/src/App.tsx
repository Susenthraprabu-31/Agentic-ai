import { useEffect } from "react";
import { RouterProvider } from "react-router-dom";
import { router } from "@/routes";
import { useAuthStore } from "@/store/authStore";

function App() {
  const initializeAuth = useAuthStore((state) => state.initializeAuth);

  useEffect(() => {
    initializeAuth();
  }, [initializeAuth]);

  return <RouterProvider router={router} />;
}

export default App;
