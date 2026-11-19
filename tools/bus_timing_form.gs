/**
 * Idavazhi: a Google Form that asks private bus owners, crews and regular
 * passengers in Kerala for a bus's route and timings.
 *
 * How to make the form (once, in your own Google account):
 *   1. Open https://script.google.com and click "New project".
 *   2. Delete the sample code, paste this whole file, and save.
 *   3. Pick createBusTimingForm in the toolbar and click Run.
 *      Google asks you to allow the script to make forms and sheets: allow it.
 *   4. Open "Execution log": it prints the link to share, the link to edit
 *      the form, and the link to the response sheet.
 *
 * One answer is one bus. People with several buses send the form again
 * ("Submit another response" is turned on). Answers land in a Google Sheet
 * named "Kerala private bus timings (responses)". The form itself does not
 * name the app.
 *
 * Anyone can answer without a Google sign-in. The phone number is optional,
 * used only to check an answer, and never published.
 */

var DISTRICTS = [
  "Thiruvananthapuram", "Kollam", "Pathanamthitta", "Alappuzha", "Kottayam",
  "Idukki", "Ernakulam", "Thrissur", "Palakkad", "Malappuram", "Kozhikode",
  "Wayanad", "Kannur", "Kasaragod"
];

var DAYS = [
  "Monday / തിങ്കൾ", "Tuesday / ചൊവ്വ", "Wednesday / ബുധൻ",
  "Thursday / വ്യാഴം", "Friday / വെള്ളി", "Saturday / ശനി", "Sunday / ഞായർ"
];

function createBusTimingForm() {
  var form = FormApp.create("Kerala private bus routes and timings / കേരളത്തിലെ സ്വകാര്യ ബസ് റൂട്ടും സമയവും");
  form.setDescription(
    "We are collecting the routes and timings of private buses in Kerala, " +
    "so that passengers can find out which bus goes where, and when.\n" +
    "കേരളത്തിലെ സ്വകാര്യ ബസുകളുടെ റൂട്ടും സമയവും ഞങ്ങൾ ശേഖരിക്കുന്നു. " +
    "ഏത് ബസ് എവിടേക്ക്, എപ്പോൾ പോകുന്നു എന്ന് യാത്രക്കാർക്ക് അറിയാൻ വേണ്ടിയാണിത്.\n\n" +
    "One form for each bus. It takes about 3 minutes.\n" +
    "ഓരോ ബസിനും ഒരു ഫോം. ഏകദേശം 3 മിനിറ്റ് മതി."
  );
  form.setProgressBar(true);
  form.setShowLinkToRespondAgain(true);
  form.setAllowResponseEdits(true);
  form.setCollectEmail(false);
  form.setConfirmationMessage(
    "Thank you! We will check the timings before using them. " +
    "Have another bus? Use \"Submit another response\".\n" +
    "നന്ദി! ഉപയോഗിക്കുന്നതിന് മുമ്പ് സമയം പരിശോധിക്കും. " +
    "മറ്റൊരു ബസ് ഉണ്ടെങ്കിൽ \"Submit another response\" അമർത്തുക."
  );

  // ---- 1. The bus ---------------------------------------------------------
  form.addSectionHeaderItem()
    .setTitle("The bus / ബസ്");

  form.addMultipleChoiceItem()
    .setTitle("Who is filling this in? / ഈ ഫോം പൂരിപ്പിക്കുന്നത് ആര്?")
    .setChoiceValues([
      "Bus owner / ബസ് ഉടമ",
      "Conductor or driver / കണ്ടക്ടർ അല്ലെങ്കിൽ ഡ്രൈവർ",
      "Regular passenger / സ്ഥിരം യാത്രക്കാർ"
    ])
    .showOtherOption(true)
    .setRequired(true);

  form.addTextItem()
    .setTitle("Bus name / ബസിന്റെ പേര്")
    .setHelpText("The name painted on the bus, e.g. St. Mary's / ബസിൽ എഴുതിയ പേര്")
    .setRequired(true);

  form.addTextItem()
    .setTitle("Registration number / രജിസ്ട്രേഷൻ നമ്പർ")
    .setHelpText("e.g. KL 07 AB 1234. Leave empty if you don't know. / അറിയില്ലെങ്കിൽ ഒഴിവാക്കാം.")
    .setValidation(FormApp.createTextValidation()
      .setHelpText("Write it like KL 07 AB 1234 / KL 07 AB 1234 എന്ന പോലെ എഴുതുക")
      .requireTextMatchesPattern("^\\s*[kK][lL][\\s.-]*\\d{1,2}[\\s.-]*[a-zA-Z]{0,3}[\\s.-]*\\d{1,4}\\s*$")
      .build());

  form.addMultipleChoiceItem()
    .setTitle("Type of service / സർവീസ് തരം")
    .setChoiceValues([
      "Ordinary / ഓർഡിനറി",
      "Fast Passenger / ഫാസ്റ്റ് പാസഞ്ചർ",
      "Limited Stop / ലിമിറ്റഡ് സ്റ്റോപ്പ്"
    ])
    .showOtherOption(true)
    .setRequired(true);

  form.addListItem()
    .setTitle("District the bus starts in / ബസ് പുറപ്പെടുന്ന ജില്ല")
    .setChoiceValues(DISTRICTS)
    .setRequired(true);

  // ---- 2. Route -----------------------------------------------------------
  form.addPageBreakItem()
    .setTitle("Route / റൂട്ട്")
    .setHelpText("Write the route in one direction. The return trip comes on the next page.\n" +
                 "ഒരു ദിശയിലേക്കുള്ള റൂട്ട് എഴുതുക. മടക്കയാത്ര അടുത്ത പേജിൽ.");

  form.addTextItem()
    .setTitle("Starts from / പുറപ്പെടുന്ന സ്ഥലം")
    .setHelpText("e.g. Aluva")
    .setRequired(true);

  form.addTextItem()
    .setTitle("Goes to / എത്തുന്ന സ്ഥലം")
    .setHelpText("e.g. Fort Kochi")
    .setRequired(true);

  form.addParagraphTextItem()
    .setTitle("Stops on the way, in order / വഴിയിലെ സ്റ്റോപ്പുകൾ, ക്രമത്തിൽ")
    .setHelpText("One stop on each line. The main stops are enough.\n" +
                 "ഓരോ വരിയിലും ഒരു സ്റ്റോപ്പ്. പ്രധാന സ്റ്റോപ്പുകൾ മതി.\n" +
                 "e.g.\nKalamassery\nEdappally\nVyttila\nThoppumpady")
    .setRequired(true);

  form.addTextItem()
    .setTitle("Main road it takes (if you know) / പോകുന്ന പ്രധാന റോഡ്")
    .setHelpText("e.g. NH 66, SH 41, via Kakkanad");

  // ---- 3. Timings ---------------------------------------------------------
  form.addPageBreakItem()
    .setTitle("Timings / സമയം");

  form.addParagraphTextItem()
    .setTitle("Departure times from the starting point/ പുറപ്പെടുന്ന സ്ഥലത്തുനിന്ന് പുറപ്പെടുന്ന സമയങ്ങൾ")
    .setHelpText("Every trip of the day, e.g. 6:15 AM, 8:40 AM, 1:05 PM, 5:30 PM\n" +
                 "ഒരു ദിവസത്തെ എല്ലാ ട്രിപ്പുകളും.")
    .setRequired(true);

  form.addParagraphTextItem()
    .setTitle("Return trip: departure times from the last stop / മടക്കയാത്ര: എത്തുന്ന സ്ഥലത്തുനിന്ന് തിരിച്ച് പുറപ്പെടുന്ന സമയങ്ങൾ")
    .setHelpText("Leave empty if the bus does not come back the same way. / അതേ വഴി തിരിച്ചു വരുന്നില്ലെങ്കിൽ ഒഴിവാക്കാം.");

  form.addTextItem()
    .setTitle("Minutes from start to end / തുടക്കം മുതൽ അവസാനം വരെ എത്ര മിനിറ്റ്")
    .setHelpText("About how long one trip takes, e.g. 75 / ഒരു ട്രിപ്പിന് ഏകദേശം എടുക്കുന്ന സമയം")
    .setValidation(FormApp.createTextValidation()
      .setHelpText("Minutes as a number, e.g. 75 / മിനിറ്റ്, അക്കത്തിൽ")
      .requireNumberBetween(1, 900)
      .build());

  form.addCheckboxItem()
    .setTitle("Days it runs / ഓടുന്ന ദിവസങ്ങൾ")
    .setChoiceValues(DAYS)
    .setRequired(true);

  form.addParagraphTextItem()
    .setTitle("Time at each stop, if you have the time card (optional) / ഓരോ സ്റ്റോപ്പിലെയും സമയം (ടൈം കാർഡ് ഉണ്ടെങ്കിൽ)")
    .setHelpText("One stop and time on each line, for one trip.\n" +
                 "ഓരോ വരിയിലും ഒരു സ്റ്റോപ്പും സമയവും.\n" +
                 "e.g.\nAluva 6:15\nKalamassery 6:35\nEdappally 6:50\nVyttila 7:10");

  form.addParagraphTextItem()
    .setTitle("Anything else? / മറ്റെന്തെങ്കിലും?")
    .setHelpText("e.g. no service on festival days, last trip only goes to Vyttila / " +
                 "ഉദാ: ഉത്സവ ദിവസങ്ങളിൽ സർവീസ് ഇല്ല");

  // ---- 4. Contact and consent ---------------------------------------------
  form.addPageBreakItem()
    .setTitle("Contact (optional) / ബന്ധപ്പെടാൻ (നിർബന്ധമില്ല)")
    .setHelpText("Only so we can check the timings with you. Never published or shared.\n" +
                 "സമയം ഉറപ്പുവരുത്താൻ മാത്രം. പ്രസിദ്ധീകരിക്കുകയോ മറ്റാർക്കും നൽകുകയോ ഇല്ല.");

  form.addTextItem()
    .setTitle("Your name / നിങ്ങളുടെ പേര്");

  form.addTextItem()
    .setTitle("Phone or WhatsApp number / ഫോൺ അല്ലെങ്കിൽ വാട്ട്സ്ആപ്പ് നമ്പർ")
    .setValidation(FormApp.createTextValidation()
      .setHelpText("A 10-digit Indian mobile number / 10 അക്ക മൊബൈൽ നമ്പർ")
      .requireTextMatchesPattern("^\\s*(\\+?91[\\s-]*)?[6-9]\\d{4}[\\s-]?\\d{5}\\s*$")
      .build());

  form.addCheckboxItem()
    .setTitle("Consent / സമ്മതം")
    .setChoiceValues([
      "These timings are correct as far as I know, and the route and timings may be published so passengers can find the bus. / " +
      "എനിക്കറിയാവുന്നിടത്തോളം ഈ സമയങ്ങൾ ശരിയാണ്. യാത്രക്കാർക്ക് ബസ് കണ്ടെത്താൻ റൂട്ടും സമയവും പ്രസിദ്ധീകരിക്കാം."
    ])
    .setRequired(true);

  // ---- Responses go to a sheet --------------------------------------------
  var sheet = SpreadsheetApp.create("Kerala private bus timings (responses)");
  form.setDestination(FormApp.DestinationType.SPREADSHEET, sheet.getId());
  form.setAcceptingResponses(true);
  // Newer Google accounts keep script-made forms unpublished until told.
  if (typeof form.setPublished === "function") {
    form.setPublished(true);
  }

  Logger.log("Share this link:  " + form.getPublishedUrl());
  Logger.log("Edit the form:    " + form.getEditUrl());
  Logger.log("Response sheet:   " + sheet.getUrl());
}
