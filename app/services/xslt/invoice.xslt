<?xml version="1.0" encoding="UTF-8"?>
<!--
  Invoice display template (XSLT 1.0) embedded into UBL-TR invoices.
  GİB's portal, integrator portals and receivers' e-invoice viewers use it to show the invoice.
-->
<xsl:stylesheet version="1.0"
    xmlns:xsl="http://www.w3.org/1999/XSL/Transform"
    xmlns:inv="urn:oasis:names:specification:ubl:schema:xsd:Invoice-2"
    xmlns:cac="urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2"
    xmlns:cbc="urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2"
    exclude-result-prefixes="inv cac cbc">
  <xsl:output method="html" encoding="UTF-8" indent="yes"/>
  <xsl:decimal-format name="tr" decimal-separator="," grouping-separator="."/>

  <xsl:template name="money">
    <xsl:param name="v"/>
    <xsl:value-of select="format-number($v, '#.##0,00', 'tr')"/>
    <xsl:text> </xsl:text>
    <xsl:choose>
      <xsl:when test="/inv:Invoice/cbc:DocumentCurrencyCode = 'TRY'">TL</xsl:when>
      <xsl:otherwise><xsl:value-of select="/inv:Invoice/cbc:DocumentCurrencyCode"/></xsl:otherwise>
    </xsl:choose>
  </xsl:template>

  <xsl:template name="party">
    <xsl:param name="p"/>
    <b>
      <xsl:choose>
        <xsl:when test="$p/cac:PartyName/cbc:Name"><xsl:value-of select="$p/cac:PartyName/cbc:Name"/></xsl:when>
        <xsl:otherwise><xsl:value-of select="concat($p/cac:Person/cbc:FirstName, ' ', $p/cac:Person/cbc:FamilyName)"/></xsl:otherwise>
      </xsl:choose>
    </b><br/>
    <xsl:value-of select="$p/cac:PostalAddress/cbc:StreetName"/><br/>
    <xsl:value-of select="$p/cac:PostalAddress/cbc:CitySubdivisionName"/> / <xsl:value-of select="$p/cac:PostalAddress/cbc:CityName"/><br/>
    <xsl:if test="$p/cac:Contact/cbc:Telephone">Tel: <xsl:value-of select="$p/cac:Contact/cbc:Telephone"/><br/></xsl:if>
    <xsl:if test="$p/cac:Contact/cbc:ElectronicMail">E-Posta: <xsl:value-of select="$p/cac:Contact/cbc:ElectronicMail"/><br/></xsl:if>
    <xsl:if test="$p/cac:PartyTaxScheme/cac:TaxScheme/cbc:Name">Vergi Dairesi: <xsl:value-of select="$p/cac:PartyTaxScheme/cac:TaxScheme/cbc:Name"/><br/></xsl:if>
    <xsl:for-each select="$p/cac:PartyIdentification/cbc:ID">
      <xsl:value-of select="@schemeID"/>: <xsl:value-of select="."/><br/>
    </xsl:for-each>
  </xsl:template>

  <xsl:template match="/inv:Invoice">
    <html>
      <head>
        <meta charset="UTF-8"/>
        <title><xsl:value-of select="cbc:ID"/></title>
        <style>
          body { font-family: Arial, Helvetica, sans-serif; font-size: 11px; color: #101828; margin: 24px; }
          table { border-collapse: collapse; font-size: inherit; }
          .top { width: 100%; }
          .top td { vertical-align: top; width: 33%; }
          .title { text-align: center; font-size: 18px; font-weight: bold; letter-spacing: 1px; }
          .meta td { border: 1px solid #98a2b3; padding: 3px 6px; }
          .meta td.k { font-weight: bold; background: #f2f4f7; }
          .cust { margin-top: 14px; border-top: 1px solid #98a2b3; border-bottom: 1px solid #98a2b3; padding: 8px 0; width: 60%; }
          .lines { width: 100%; margin-top: 14px; }
          .lines th { background: #f2f4f7; border: 1px solid #98a2b3; padding: 4px; text-align: left; }
          .lines td { border: 1px solid #98a2b3; padding: 4px; }
          .r { text-align: right; white-space: nowrap; }
          .sum { margin: 10px 0 0 auto; }
          .sum td { border: 1px solid #98a2b3; padding: 4px 8px; }
          .sum td.k { font-weight: bold; background: #f2f4f7; }
          .notes { margin-top: 14px; border: 1px solid #98a2b3; padding: 6px 8px; }
        </style>
      </head>
      <body>
        <table class="top"><tr>
          <td><!--LOGO--><xsl:call-template name="party"><xsl:with-param name="p" select="cac:AccountingSupplierParty/cac:Party"/></xsl:call-template></td>
          <td class="title">
            <xsl:choose>
              <xsl:when test="cbc:ProfileID = 'EARSIVFATURA'">e-ARŞİV FATURA</xsl:when>
              <xsl:otherwise>e-FATURA</xsl:otherwise>
            </xsl:choose>
          </td>
          <td>
            <table class="meta" align="right">
              <tr><td class="k">Özelleştirme No</td><td><xsl:value-of select="cbc:CustomizationID"/></td></tr>
              <tr><td class="k">Senaryo</td><td><xsl:value-of select="cbc:ProfileID"/></td></tr>
              <tr><td class="k">Fatura Tipi</td><td><xsl:value-of select="cbc:InvoiceTypeCode"/></td></tr>
              <tr><td class="k">Fatura No</td><td><xsl:value-of select="cbc:ID"/></td></tr>
              <tr><td class="k">Fatura Tarihi</td><td>
                <xsl:value-of select="concat(substring(cbc:IssueDate, 9, 2), '.', substring(cbc:IssueDate, 6, 2), '.', substring(cbc:IssueDate, 1, 4))"/>
                <xsl:text> </xsl:text><xsl:value-of select="substring(cbc:IssueTime, 1, 5)"/></td></tr>
              <xsl:if test="cac:PaymentMeans/cbc:PaymentDueDate">
                <tr><td class="k">Son Ödeme</td><td><xsl:value-of select="cac:PaymentMeans/cbc:PaymentDueDate"/></td></tr>
              </xsl:if>
              <xsl:for-each select="cac:BillingReference/cac:InvoiceDocumentReference">
                <tr><td class="k">İade Edilen Fatura</td><td><xsl:value-of select="cbc:ID"/></td></tr>
              </xsl:for-each>
            </table>
          </td>
        </tr></table>

        <div class="cust">
          <b>SAYIN</b><br/>
          <xsl:call-template name="party"><xsl:with-param name="p" select="cac:AccountingCustomerParty/cac:Party"/></xsl:call-template>
        </div>
        <p><b>ETTN:</b> <xsl:value-of select="cbc:UUID"/></p>

        <table class="lines">
          <tr><th>Sıra</th><th>Mal / Hizmet</th><th class="r">Miktar</th><th class="r">Birim Fiyat</th>
              <th class="r">İskonto</th><th class="r">KDV %</th><th class="r">KDV</th><th class="r">Tutar</th></tr>
          <xsl:for-each select="cac:InvoiceLine">
            <tr>
              <td><xsl:value-of select="cbc:ID"/></td>
              <td><xsl:value-of select="cac:Item/cbc:Name"/></td>
              <td class="r"><xsl:value-of select="format-number(cbc:InvoicedQuantity, '#.##0,###', 'tr')"/>
                <xsl:text> </xsl:text>
                <xsl:choose>
                  <xsl:when test="cbc:InvoicedQuantity/@unitCode = 'C62'">Adet</xsl:when>
                  <xsl:when test="cbc:InvoicedQuantity/@unitCode = 'HUR'">Saat</xsl:when>
                  <xsl:otherwise><xsl:value-of select="cbc:InvoicedQuantity/@unitCode"/></xsl:otherwise>
                </xsl:choose></td>
              <td class="r"><xsl:call-template name="money"><xsl:with-param name="v" select="cac:Price/cbc:PriceAmount"/></xsl:call-template></td>
              <td class="r"><xsl:if test="cac:AllowanceCharge/cbc:Amount"><xsl:call-template name="money"><xsl:with-param name="v" select="cac:AllowanceCharge/cbc:Amount"/></xsl:call-template></xsl:if></td>
              <td class="r"><xsl:value-of select="cac:TaxTotal/cac:TaxSubtotal/cbc:Percent"/></td>
              <td class="r"><xsl:call-template name="money"><xsl:with-param name="v" select="cac:TaxTotal/cbc:TaxAmount"/></xsl:call-template></td>
              <td class="r"><xsl:call-template name="money"><xsl:with-param name="v" select="cbc:LineExtensionAmount"/></xsl:call-template></td>
            </tr>
          </xsl:for-each>
        </table>

        <table class="sum">
          <tr><td class="k">Mal Hizmet Toplam Tutarı</td><td class="r"><xsl:call-template name="money"><xsl:with-param name="v" select="cac:LegalMonetaryTotal/cbc:LineExtensionAmount"/></xsl:call-template></td></tr>
          <xsl:if test="cac:LegalMonetaryTotal/cbc:AllowanceTotalAmount &gt; 0">
            <tr><td class="k">Toplam İskonto</td><td class="r"><xsl:call-template name="money"><xsl:with-param name="v" select="cac:LegalMonetaryTotal/cbc:AllowanceTotalAmount"/></xsl:call-template></td></tr>
          </xsl:if>
          <xsl:for-each select="cac:TaxTotal/cac:TaxSubtotal">
            <tr><td class="k">Hesaplanan KDV (%<xsl:value-of select="cbc:Percent"/>)</td><td class="r"><xsl:call-template name="money"><xsl:with-param name="v" select="cbc:TaxAmount"/></xsl:call-template></td></tr>
          </xsl:for-each>
          <xsl:for-each select="cac:WithholdingTaxTotal/cac:TaxSubtotal">
            <tr><td class="k">Tevkifat (<xsl:value-of select="cac:TaxCategory/cac:TaxScheme/cbc:TaxTypeCode"/>)</td><td class="r"><xsl:call-template name="money"><xsl:with-param name="v" select="cbc:TaxAmount"/></xsl:call-template></td></tr>
          </xsl:for-each>
          <tr><td class="k">Vergiler Dahil Toplam</td><td class="r"><xsl:call-template name="money"><xsl:with-param name="v" select="cac:LegalMonetaryTotal/cbc:TaxInclusiveAmount"/></xsl:call-template></td></tr>
          <tr><td class="k">Ödenecek Tutar</td><td class="r"><b><xsl:call-template name="money"><xsl:with-param name="v" select="cac:LegalMonetaryTotal/cbc:PayableAmount"/></xsl:call-template></b></td></tr>
        </table>

        <div class="notes">
          <xsl:for-each select="cbc:Note"><xsl:value-of select="."/><br/></xsl:for-each>
          <xsl:for-each select="cac:PaymentMeans/cac:PayeeFinancialAccount/cbc:ID">IBAN: <xsl:value-of select="."/><br/></xsl:for-each>
        </div>
      </body>
    </html>
  </xsl:template>
</xsl:stylesheet>
