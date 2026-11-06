package demo;

public class Legacy {
    public Document parse(InputStream in) throws Exception {
        DocumentBuilderFactory f = DocumentBuilderFactory.newInstance();
        return f.newDocumentBuilder().parse(in);
    }

    public Object load(InputStream in) throws Exception {
        return new ObjectInputStream(in).readObject();
    }

    public void ping(String host) throws Exception {
        Runtime.getRuntime().exec("ping -c 1 " + host);
        new ProcessBuilder("sh", "-c", cmd).start();
    }

    public void safeRun() throws Exception {
        new ProcessBuilder("ls", "-la").start();
    }
}
