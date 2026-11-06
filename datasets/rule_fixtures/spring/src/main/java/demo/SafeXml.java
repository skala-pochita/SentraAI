package demo;

// 음성: XXE 방어 설정이 있는 파서
public class SafeXml {
    public Document parse(InputStream in) throws Exception {
        DocumentBuilderFactory f = DocumentBuilderFactory.newInstance();
        f.setFeature("http://apache.org/xml/features/disallow-doctype-decl", true);
        return f.newDocumentBuilder().parse(in);
    }
}
