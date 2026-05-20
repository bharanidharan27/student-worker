import { configureStore } from "@reduxjs/toolkit";

import { consoleApi } from "../services/api";

export const store = configureStore({
  reducer: {
    [consoleApi.reducerPath]: consoleApi.reducer
  },
  middleware: (getDefaultMiddleware) => getDefaultMiddleware().concat(consoleApi.middleware)
});

export type RootState = ReturnType<typeof store.getState>;
export type AppDispatch = typeof store.dispatch;
