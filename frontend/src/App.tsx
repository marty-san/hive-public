import { BrowserRouter as Router, Routes, Route, Navigate } from 'react-router-dom';
import { Layout } from './components/Layout';
import { Agents } from './pages/Agents';
import { Chat } from './pages/Chat';

function App() {
  return (
    <Router
      future={{
        v7_startTransition: true,
        v7_relativeSplatPath: true,
      }}
    >
      <Layout>
        <Routes>
          <Route path="/" element={<Navigate to="/chat" replace />} />
          <Route path="/agents" element={<Agents />} />
          <Route path="/chat/:conversationId?" element={<Chat />} />
        </Routes>
      </Layout>
    </Router>
  );
}

export default App;
